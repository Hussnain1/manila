# Copyright 2011-2012 OpenStack LLC.
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
"""
Tests For Capacity Weigher.
"""

import mock
from oslo.config import cfg

from manila import context
from manila.openstack.common.scheduler import weights
from manila.scheduler.weights import capacity
from manila import test
from manila.tests.scheduler import fakes

CONF = cfg.CONF


class CapacityWeigherTestCase(test.TestCase):
    def setUp(self):
        super(CapacityWeigherTestCase, self).setUp()
        self.host_manager = fakes.FakeHostManager()
        self.weight_handler = weights.HostWeightHandler(
            'manila.scheduler.weights')

    def _get_weighed_host(self, hosts, weight_properties=None):
        if weight_properties is None:
            weight_properties = {}
        return self.weight_handler.get_weighed_objects(
            [capacity.CapacityWeigher],
            hosts,
            weight_properties)[0]

    @mock.patch('manila.db.sqlalchemy.api.service_get_all_by_topic')
    def _get_all_hosts(self, _mock_service_get_all_by_topic, disabled=False):
        ctxt = context.get_admin_context()
        fakes.mock_host_manager_db_calls(_mock_service_get_all_by_topic,
                                         disabled=disabled)
        host_states = self.host_manager.get_all_host_states_share(ctxt)
        _mock_service_get_all_by_topic.assert_called_once_with(
            ctxt, CONF.share_topic)
        return host_states

    def test_default_of_spreading_first(self):
        hostinfo_list = self._get_all_hosts()

        # host1: free_capacity_gb=1024, free=1024*(1-0.1)
        # host2: free_capacity_gb=300, free=300*(1-0.1)
        # host3: free_capacity_gb=512, free=512
        # host4: free_capacity_gb=200, free=200*(1-0.05)

        # so, host1 should win:
        weighed_host = self._get_weighed_host(hostinfo_list)
        self.assertEqual(weighed_host.weight, 1.0)
        self.assertEqual(weighed_host.obj.host, 'host1')

    def test_capacity_weight_multiplier1(self):
        self.flags(capacity_weight_multiplier=-1.0)
        hostinfo_list = self._get_all_hosts()

        # host1: free_capacity_gb=1024, free=-1024*(1-0.1)
        # host2: free_capacity_gb=300, free=-300*(1-0.1)
        # host3: free_capacity_gb=512, free=-512
        # host4: free_capacity_gb=200, free=-200*(1-0.05)

        # so, host4 should win:
        weighed_host = self._get_weighed_host(hostinfo_list)
        self.assertEqual(weighed_host.weight, 0.0)
        self.assertEqual(weighed_host.obj.host, 'host4')

    def test_capacity_weight_multiplier2(self):
        self.flags(capacity_weight_multiplier=2.0)
        hostinfo_list = self._get_all_hosts()

        # host1: free_capacity_gb=1024, free=1024*(1-0.1)*2
        # host2: free_capacity_gb=300, free=300*(1-0.1)*2
        # host3: free_capacity_gb=512, free=512*2
        # host4: free_capacity_gb=200, free=200*(1-0.05)*2

        # so, host1 should win:
        weighed_host = self._get_weighed_host(hostinfo_list)
        self.assertEqual(weighed_host.weight, 2.0)
        self.assertEqual(weighed_host.obj.host, 'host1')
