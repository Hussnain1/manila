# Copyright (c) 2014 NetApp, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Module for managing nova instances for share drivers."""

import os
import socket
import threading
import time

import netaddr
from oslo.config import cfg
from oslo.utils import importutils
import six

from manila.common import constants
from manila import compute
from manila import context
from manila import exception
from manila.i18n import _
from manila.i18n import _LW
from manila.network.linux import ip_lib
from manila.network.neutron import api as neutron
from manila.openstack.common import log as logging
from manila import utils


LOG = logging.getLogger(__name__)

server_opts = [
    cfg.StrOpt('service_image_name',
               default='manila-service-image',
               help="Name of image in glance, that will be used to create "
               "service instance."),
    cfg.StrOpt('service_instance_name_template',
               default='manila_service_instance_%s',
               help="Name of service instance."),
    cfg.StrOpt('service_instance_user',
               help="User in service instance."),
    cfg.StrOpt('service_instance_password',
               default=None,
               help="Password to service instance user."),
    cfg.StrOpt('manila_service_keypair_name',
               default='manila-service',
               help="Name of keypair that will be created and used "
               "for service instance."),
    cfg.StrOpt('path_to_public_key',
               default='~/.ssh/id_rsa.pub',
               help="Path to hosts public key."),
    cfg.StrOpt('path_to_private_key',
               default='~/.ssh/id_rsa',
               help="Path to hosts private key."),
    cfg.IntOpt('max_time_to_build_instance',
               default=300,
               help="Maximum time to wait for creating service instance."),
    cfg.StrOpt('service_instance_security_group',
               default="manila-service",
               help="Name of security group, that will be used for "
               "service instance creation."),
    cfg.IntOpt('service_instance_flavor_id',
               default=100,
               help="ID of flavor, that will be used for service instance "
               "creation."),
    cfg.StrOpt('service_network_name',
               default='manila_service_network',
               help="Name of manila service network."),
    cfg.StrOpt('service_network_cidr',
               default='10.254.0.0/16',
               help="CIDR of manila service network."),
    cfg.IntOpt('service_network_division_mask',
               default=28,
               help="This mask is used for dividing service network into "
                    "subnets, IP capacity of subnet with this mask directly "
                    "defines possible amount of created service VMs "
                    "per tenant's subnet."),
    cfg.StrOpt('interface_driver',
               default='manila.network.linux.interface.OVSInterfaceDriver',
               help="Vif driver."),
    cfg.BoolOpt('connect_share_server_to_tenant_network',
                default=False,
                help='Attach share server directly to share network.'),
]

CONF = cfg.CONF
CONF.register_opts(server_opts)

lock = threading.Lock()


class ServiceInstanceManager(object):
    """Manages nova instances for various share drivers.

    This class provides following external methods:

    1. set_up_service_instance: creates instance and sets up share
       infrastructure.
    2. ensure_service_instance: ensure service instance is available.
    3. delete_service_instance: removes service instance and network
       infrastructure.
    """

    def get_config_option(self, key):
        """Returns value of config option.

        :param key: key of config' option.
        :returns: str -- value of config's option.
                  first priority is driver's config,
                  second priority is global config.
        """
        value = None
        if self.driver_config:
            value = self.driver_config.safe_get(key)
        else:
            value = CONF.get(key)
        return value

    def __init__(self, db, *args, **kwargs):
        """Do initialization."""
        super(ServiceInstanceManager, self).__init__()
        self.driver_config = None
        if "driver_config" in kwargs:
            self.driver_config = kwargs["driver_config"]
        if not self.get_config_option("service_instance_user"):
            raise exception.ServiceInstanceException(_('Service instance user '
                                                       'is not specified'))
        self.admin_context = context.get_admin_context()
        self._execute = utils.execute
        self.compute_api = compute.API()
        self.neutron_api = neutron.API()
        self.db = db
        attempts = 5
        while attempts:
            try:
                self.service_tenant_id = self.neutron_api.admin_tenant_id
                break
            except exception.NetworkException:
                LOG.debug('Connection to neutron failed.')
                attempts -= 1
                time.sleep(3)
        else:
            raise exception.ServiceInstanceException(_('Can not receive '
                                                       'service tenant id.'))
        self.service_network_id = self._get_service_network()
        self.vif_driver = importutils.import_class(
            self.get_config_option("interface_driver"))()
        self._setup_connectivity_with_service_instances()
        self.max_time_to_build_instance = self.get_config_option(
            "max_time_to_build_instance")
        self.path_to_private_key = self.get_config_option(
            "path_to_private_key")
        self.path_to_public_key = self.get_config_option("path_to_public_key")
        self.connect_share_server_to_tenant_network = self.get_config_option(
            'connect_share_server_to_tenant_network')

    @utils.synchronized("service_instance_get_service_network", external=True)
    def _get_service_network(self):
        """Finds existing or creates new service network."""
        service_network_name = self.get_config_option("service_network_name")
        networks = [network for network in self.neutron_api.
                    get_all_tenant_networks(self.service_tenant_id)
                    if network['name'] == service_network_name]
        if len(networks) > 1:
            raise exception.ServiceInstanceException(_('Ambiguous service '
                                                       'networks.'))
        elif not networks:
            return self.neutron_api.network_create(self.service_tenant_id,
                                                   service_network_name)['id']
        else:
            return networks[0]['id']

    def _get_service_instance_name(self, share_server_id):
        """Returns service vms name."""
        if self.driver_config:
            # Make service instance name unique for multibackend installation
            name = "%s_%s" % (self.driver_config.config_group,
                              share_server_id)
        else:
            name = share_server_id
        return self.get_config_option("service_instance_name_template") % name

    def _get_server_ip(self, server):
        """Returns service vms ip address."""
        net_name = self.get_config_option("service_network_name")
        net_ips = []
        if 'networks' in server and net_name in server['networks']:
            net_ips = server['networks'][net_name]
        elif 'addresses' in server and net_name in server['addresses']:
            net_ips = [addr['addr'] for addr in server['addresses'][net_name]]
        if not net_ips:
            msg = _("Failed to get service instance ip address. "
                    "Service network name is '%(net_name)s' "
                    "and provided data are '%(data)s'.")
            msg = msg % {'net_name': net_name, 'data': six.text_type(server)}
            raise exception.ServiceInstanceException(msg)
        return net_ips[0]

    @utils.synchronized(
        "service_instance_get_or_create_security_group", external=True)
    def _get_or_create_security_group(self, context, name=None,
                                      description=None):
        """Get or create security group for service_instance.

        :param context: context, that should be used
        :param name: this is used for selection/creation of sec.group
        :param description: this is used on sec.group creation step only
        :returns: SecurityGroup -- security group instance from Nova
        :raises: exception.ServiceInstanceException.
        """
        name = name or self.get_config_option(
            "service_instance_security_group")
        if not name:
            LOG.warning(_LW("Name for service instance security group is not "
                            "provided. Skipping security group step."))
            return None
        s_groups = [s for s in self.compute_api.security_group_list(context)
                    if s.name == name]
        if not s_groups:
            # Creating security group
            if not description:
                description = "This security group is intended "\
                              "to be used by share service."
            LOG.debug("Creating security group with name '%s'.", name)
            sg = self.compute_api.security_group_create(
                context, name, description)
            for protocol, ports in constants.SERVICE_INSTANCE_SECGROUP_DATA:
                self.compute_api.security_group_rule_create(
                    context,
                    parent_group_id=sg.id,
                    ip_protocol=protocol,
                    from_port=ports[0],
                    to_port=ports[1],
                    cidr="0.0.0.0/0",
                )
        elif len(s_groups) > 1:
            msg = _("Ambiguous security_groups.")
            raise exception.ServiceInstanceException(msg)
        else:
            sg = s_groups[0]
        return sg

    def ensure_service_instance(self, context, server):
        """Ensures that server exists and active."""
        try:
            inst = self.compute_api.server_get(self.admin_context,
                                               server['instance_id'])
        except exception.InstanceNotFound:
            LOG.warning(_LW("Service instance %s does not exist."),
                        server['instance_id'])
            return False
        if inst['status'] == 'ACTIVE':
            return self._check_server_availability(server)
        return False

    def _delete_server(self, context, server_id):
        """Deletes the server."""
        self.compute_api.server_delete(context, server_id)
        t = time.time()
        while time.time() - t < self.max_time_to_build_instance:
            try:
                self.compute_api.server_get(context, server_id)
            except exception.InstanceNotFound:
                LOG.debug('Service instance was deleted succesfully.')
                break
            time.sleep(1)
        else:
            raise exception.ServiceInstanceException(
                _('Instance have not '
                  'been deleted in %ss. Giving up.') %
                self.max_time_to_build_instance)

    def set_up_service_instance(self, context, instance_name, neutron_net_id,
                                neutron_subnet_id):
        """Finds or creates and sets up service vm.

        :param context: defines context, that should be used
        :param instance_name: provides name for service VM
        :param neutron_net_id: provides network id for service VM
        :param neutron_subnet_id: provides subnet id for service VM
        :returns: dict with service instance details
        :raises: exception.ServiceInstanceException
        """
        server = self._create_service_instance(context,
                                               instance_name,
                                               neutron_net_id,
                                               neutron_subnet_id)

        instance_details = {'instance_id': server['id'],
                            'ip': server['ip'],
                            'pk_path': server['pk_path'],
                            'subnet_id': server['subnet_id'],
                            'router_id': server['router_id'],
                            'password': self.get_config_option(
                                'service_instance_password'),
                            'username': self.get_config_option(
                                'service_instance_user'),
                            'public_address': server['public_address']}
        for key in ('password', 'pk_path'):
            if not instance_details[key]:
                instance_details.pop(key)

        return instance_details

    @utils.synchronized("service_instance_get_key", external=True)
    def _get_key(self, context):
        """Get ssh key.

        :param context: defines context, that should be used
        :returns: tuple with keypair name and path to private key.
        """
        if not (self.path_to_public_key and self.path_to_private_key):
            return (None, None)
        path_to_public_key = os.path.expanduser(self.path_to_public_key)
        path_to_private_key = os.path.expanduser(self.path_to_private_key)
        if (not os.path.exists(path_to_public_key) or
                not os.path.exists(path_to_private_key)):
            return (None, None)
        keypair_name = self.get_config_option("manila_service_keypair_name")
        keypairs = [k for k in self.compute_api.keypair_list(context)
                    if k.name == keypair_name]
        if len(keypairs) > 1:
            raise exception.ServiceInstanceException(_('Ambiguous keypairs.'))

        public_key, __ = self._execute('cat', path_to_public_key)
        if not keypairs:
            keypair = self.compute_api.keypair_import(context,
                                                      keypair_name,
                                                      public_key)
        else:
            keypair = keypairs[0]
            if keypair.public_key != public_key:
                LOG.debug('Public key differs from existing keypair. '
                          'Creating new keypair.')
                self.compute_api.keypair_delete(context, keypair.id)
                keypair = self.compute_api.keypair_import(context,
                                                          keypair_name,
                                                          public_key)
        return keypair.name, path_to_private_key

    def _get_service_image(self, context):
        """Returns ID of service image for service vm creating."""
        service_image_name = self.get_config_option("service_image_name")
        images = [image.id for image in self.compute_api.image_list(context)
                  if image.name == service_image_name]
        if len(images) == 1:
            return images[0]
        elif not images:
            raise exception.ServiceInstanceException(_('No appropriate '
                                                       'image was found.'))
        else:
            raise exception.ServiceInstanceException(
                _('Ambiguous image name.'))

    def _create_service_instance(self, context, instance_name, neutron_net_id,
                                 neutron_subnet_id):
        """Creates service vm and sets up networking for it."""
        service_image_id = self._get_service_image(context)

        with lock:
            key_name, key_path = self._get_key(context)
            if not (self.get_config_option("service_instance_password") or
                    key_name):
                raise exception.ServiceInstanceException(
                    _('Neither service '
                      'instance password nor key are available.'))
            if not key_path:
                str_params = {
                    'private_path': self.path_to_private_key,
                    'public_path': self.path_to_public_key,
                }
                LOG.warning(_LW('No key path is available. May be '
                                'non-existent key path is provided. Check '
                                'path_to_private_key (current value '
                                '%(private_path)s) and '
                                'path_to_public_key (current value '
                                '%(public_path)s) in manila '
                                'configuration file.'), str_params)

            security_group = self._get_or_create_security_group(context)
            network_data = self._setup_network_for_instance(neutron_net_id,
                                                            neutron_subnet_id)
            try:
                self._setup_connectivity_with_service_instances()
            except Exception as e:
                LOG.debug(e)
                for port in network_data['ports']:
                    self.neutron_api.delete_port(port['id'])
                raise

        service_instance = self.compute_api.server_create(
            context,
            name=instance_name,
            image=service_image_id,
            flavor=self.get_config_option("service_instance_flavor_id"),
            key_name=key_name,
            nics=[{'port-id': port['id']} for port in network_data['ports']])

        t = time.time()
        while time.time() - t < self.max_time_to_build_instance:
            # NOTE(vponomaryov): emptiness of 'networks' field is checked as
            #                    workaround for nova/neutron bug #1210483.
            if (service_instance['status'] == 'ACTIVE' and
                    service_instance.get('networks', {})):
                break
            if service_instance['status'] == 'ERROR':
                raise exception.ServiceInstanceException(
                    _('Failed to build service instance.'))
            time.sleep(1)
            try:
                service_instance = self.compute_api.server_get(
                    context,
                    service_instance['id'])
            except exception.InstanceNotFound as e:
                LOG.debug(e)
        else:
            raise exception.ServiceInstanceException(
                _('Instance have not been spawned in %ss. Giving up.') %
                self.max_time_to_build_instance)

        if security_group:
            LOG.debug("Adding security group "
                      "'%s' to server '%s'." % (security_group.id,
                                                service_instance["id"]))
            self.compute_api.add_security_group_to_server(
                context,
                service_instance["id"], security_group.id)

        router, service_subnet, service_port = (
            network_data['router'], network_data['service_subnet'],
            network_data['service_port']
        )

        service_instance['ip'] = self._get_server_ip(service_instance)
        service_instance['pk_path'] = key_path
        service_instance['router_id'] = router['id']
        service_instance['subnet_id'] = service_subnet['id']
        service_instance['port_id'] = service_port['id']

        try:
            public_ip = network_data['public_port']
        except KeyError:
            public_ip = network_data['service_port']
        public_ip = public_ip['fixed_ips']
        public_ip = public_ip[0]
        public_ip = public_ip['ip_address']

        service_instance['public_address'] = public_ip

        if not self._check_server_availability(service_instance):
            raise exception.ServiceInstanceException(
                _('SSH connection have not been '
                  'established in %ss. Giving up.') %
                self.max_time_to_build_instance)

        return service_instance

    def _check_server_availability(self, server):
        t = time.time()
        while time.time() - t < self.max_time_to_build_instance:
            LOG.debug('Checking service vm availablity.')
            try:
                socket.socket().connect((server['ip'], 22))
                LOG.debug('Service vm is available via ssh.')
                return True
            except socket.error as e:
                LOG.debug(e)
                LOG.debug('Server is not available through ssh. Waiting...')
                time.sleep(5)
        return False

    @utils.synchronized(
        "service_instance_setup_network_for_instance", external=True)
    def _setup_network_for_instance(self, neutron_net_id, neutron_subnet_id):
        """Sets up network for service vm."""

        network_data = dict()

        # We get/create subnet for service instance that is routed to
        # subnet provided in args.
        subnet_name = "routed_to_%s" % neutron_subnet_id
        service_subnet = self._get_service_subnet(subnet_name)
        if not service_subnet:
            service_subnet = self.neutron_api.subnet_create(
                self.service_tenant_id,
                self.service_network_id,
                subnet_name,
                self._get_cidr_for_subnet()
            )

        network_data['service_subnet'] = service_subnet
        network_data['router'] = router = self._get_private_router(
            neutron_net_id, neutron_subnet_id)
        try:
            self.neutron_api.router_add_interface(router['id'],
                                                  service_subnet['id'])
        except exception.NetworkException as e:
            if e.kwargs['code'] != 400:
                raise
            LOG.debug('Subnet %(subnet_id)s is already attached to the '
                      'router %(router_id)s.',
                      {'subnet_id': service_subnet['id'],
                       'router_id': router['id']})

        network_data['service_port'] = self.neutron_api.create_port(
            self.service_tenant_id, self.service_network_id,
            subnet_id=service_subnet['id'], device_owner='manila')

        if self.connect_share_server_to_tenant_network:
            network_data['public_port'] = self.neutron_api.create_port(
                self.service_tenant_id, neutron_net_id,
                subnet_id=neutron_subnet_id, device_owner='manila')

        network_data['ports'] = ports = list()
        ports.append(network_data['service_port'])
        try:
            ports.append(network_data['public_port'])
        except KeyError:
            pass

        return network_data

    @utils.synchronized("service_instance_get_private_router", external=True)
    def _get_private_router(self, neutron_net_id, neutron_subnet_id):
        """Returns router attached to private subnet gateway."""
        private_subnet = self.neutron_api.get_subnet(neutron_subnet_id)
        if not private_subnet['gateway_ip']:
            raise exception.ServiceInstanceException(
                _('Subnet must have gateway.'))
        private_network_ports = [p for p in self.neutron_api.list_ports(
                                 network_id=neutron_net_id)]
        for p in private_network_ports:
            fixed_ip = p['fixed_ips'][0]
            if (fixed_ip['subnet_id'] == private_subnet['id'] and
                    fixed_ip['ip_address'] == private_subnet['gateway_ip']):
                private_subnet_gateway_port = p
                break
        else:
            raise exception.ServiceInstanceException(
                _('Subnet gateway is not attached the router.'))
        private_subnet_router = self.neutron_api.show_router(
            private_subnet_gateway_port['device_id'])
        return private_subnet_router

    def _setup_connectivity_with_service_instances(self):
        """Sets up connectivity with service instances.

        Creates creating port in service network, creating and setting up
        required network devices.
        """
        port = self._get_service_port()
        port = self._add_fixed_ips_to_service_port(port)
        interface_name = self.vif_driver.get_device_name(port)
        self.vif_driver.plug(interface_name, port['id'], port['mac_address'])
        ip_cidrs = []
        for fixed_ip in port['fixed_ips']:
            subnet = self.neutron_api.get_subnet(fixed_ip['subnet_id'])
            net = netaddr.IPNetwork(subnet['cidr'])
            ip_cidr = '%s/%s' % (fixed_ip['ip_address'], net.prefixlen)
            ip_cidrs.append(ip_cidr)

        self.vif_driver.init_l3(interface_name, ip_cidrs)

        # ensure that interface is first in the list
        device = ip_lib.IPDevice(interface_name)
        device.route.pullup_route(interface_name)

        # here we are checking for garbage devices from removed service port
        self._remove_outdated_interfaces(device)

    @utils.synchronized(
        "service_instance_remove_outdated_interfaces", external=True)
    def _remove_outdated_interfaces(self, device):
        """Finds and removes unused network device."""
        list_dev = []
        for dev in ip_lib.IPWrapper().get_devices():
            if dev.name != device.name and dev.name[:3] == device.name[:3]:
                cidr_set = set()
                for a in dev.addr.list():
                    if a['ip_version'] == 4:
                        cidr_set.add(str(netaddr.IPNetwork(a['cidr']).cidr))
                list_dev.append((dev.name, cidr_set))
        device_cidr_set = set(str(netaddr.IPNetwork(a['cidr']).cidr)
                              for a in device.addr.list()
                              if a['ip_version'] == 4)

        for dev_name, cidr_set in list_dev:
            if device_cidr_set & cidr_set:
                self.vif_driver.unplug(dev_name)

    @utils.synchronized("service_instance_get_service_port", external=True)
    def _get_service_port(self):
        """Find or creates service neutron port.

        This port will be used for connectivity with service instances.
        """
        ports = [port for port in self.neutron_api.
                 list_ports(device_id='manila-share')]
        if len(ports) > 1:
            raise exception.ServiceInstanceException(
                _('Error. Ambiguous service ports.'))
        elif not ports:
            try:
                stdout, stderr = self._execute('hostname')
                host = stdout.strip()
            except exception.ProcessExecutionError as e:
                msg = _('Unable to get host. %s') % e.stderr
                raise exception.ManilaException(msg)
            port = self.neutron_api.create_port(
                self.service_tenant_id,
                self.service_network_id,
                device_id='manila-share',
                device_owner='manila:share',
                host_id=host)
        else:
            port = ports[0]
        return port

    @utils.synchronized(
        "service_instance_add_fixed_ips_to_service_port", external=True)
    def _add_fixed_ips_to_service_port(self, port):
        network = self.neutron_api.get_network(self.service_network_id)
        subnets = set(network['subnets'])
        port_fixed_ips = []
        for fixed_ip in port['fixed_ips']:
            port_fixed_ips.append({'subnet_id': fixed_ip['subnet_id'],
                                   'ip_address': fixed_ip['ip_address']})
            if fixed_ip['subnet_id'] in subnets:
                subnets.remove(fixed_ip['subnet_id'])

        # If there are subnets here that means that
        # we need to add those to the port and call update.
        if subnets:
            port_fixed_ips.extend([dict(subnet_id=s) for s in subnets])
            port = self.neutron_api.update_port_fixed_ips(
                port['id'], {'fixed_ips': port_fixed_ips})

        return port

    def _get_cidr_for_subnet(self):
        """Returns not used cidr for service subnet creating."""
        subnets = self._get_all_service_subnets()
        used_cidrs = set(subnet['cidr'] for subnet in subnets)
        serv_cidr = netaddr.IPNetwork(
            self.get_config_option("service_network_cidr"))
        division_mask = self.get_config_option("service_network_division_mask")
        for subnet in serv_cidr.subnet(division_mask):
            cidr = str(subnet.cidr)
            if cidr not in used_cidrs:
                return cidr
        else:
            raise exception.ServiceInstanceException(_('No available cidrs.'))

    def delete_service_instance(self, context, instance_id, subnet_id,
                                router_id):
        """Removes share infrastructure.

        Deletes service vm and subnet, associated to share network.
        """
        self._delete_server(context, instance_id)
        if router_id and subnet_id:
            try:
                self.neutron_api.router_remove_interface(router_id,
                                                         subnet_id)
            except exception.NetworkException as e:
                if e.kwargs['code'] != 404:
                    raise
                LOG.debug('Subnet %(subnet_id)s is not attached to the '
                          'router %(router_id)s.',
                          {'subnet_id': subnet_id,
                           'router_id': router_id})
            self.neutron_api.update_subnet(subnet_id, '')

    @utils.synchronized(
        "service_instance_get_all_service_subnets", external=True)
    def _get_all_service_subnets(self):
        service_network = self.neutron_api.get_network(self.service_network_id)
        return [self.neutron_api.get_subnet(subnet_id)
                for subnet_id in service_network['subnets']]

    @utils.synchronized("service_instance_get_service_subnet", external=True)
    def _get_service_subnet(self, subnet_name):
        all_service_subnets = self._get_all_service_subnets()
        service_subnets = [subnet for subnet in all_service_subnets
                           if subnet['name'] == subnet_name]
        if len(service_subnets) == 1:
            return service_subnets[0]
        elif not service_subnets:
            unused_service_subnets = [subnet for subnet in all_service_subnets
                                      if subnet['name'] == '']
            if unused_service_subnets:
                service_subnet = unused_service_subnets[0]
                self.neutron_api.update_subnet(service_subnet['id'],
                                               subnet_name)
                return service_subnet
            return None
        else:
            raise exception.ServiceInstanceException(_('Ambiguous service '
                                                       'subnets.'))
