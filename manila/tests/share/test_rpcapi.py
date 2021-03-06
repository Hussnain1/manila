# Copyright 2013 NetApp
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
Unit Tests for manila.share.rpcapi.
"""

import copy

from oslo.config import cfg
from oslo.serialization import jsonutils
import six

from manila import context
from manila import db
from manila.share import rpcapi as share_rpcapi
from manila import test

CONF = cfg.CONF


class ShareRpcAPITestCase(test.TestCase):

    def setUp(self):
        super(ShareRpcAPITestCase, self).setUp()
        self.context = context.get_admin_context()
        shr = {}
        shr['host'] = 'fake_host'
        shr['availability_zone'] = CONF.storage_availability_zone
        shr['status'] = "available"
        share = db.share_create(self.context, shr)
        acs = {}
        acs['access_type'] = "ip"
        acs['access_to'] = "123.123.123.123"
        acs['share_id'] = share['id']
        access = db.share_access_create(self.context, acs)
        snap = {}
        snap['share_id'] = share['id']
        snapshot = db.share_snapshot_create(self.context, snap)
        server = {'id': 'fake_share_server_id', 'host': 'fake_host'}
        share_server = db.share_server_create(self.context, server)
        self.fake_share = jsonutils.to_primitive(share)
        self.fake_access = jsonutils.to_primitive(access)
        self.fake_snapshot = jsonutils.to_primitive(snapshot)
        self.fake_share_server = jsonutils.to_primitive(share_server)
        self.ctxt = context.RequestContext('fake_user', 'fake_project')
        self.rpcapi = share_rpcapi.ShareAPI()

    def test_serialized_share_has_id(self):
        self.assertTrue('id' in self.fake_share)

    def _test_share_api(self, method, rpc_method, **kwargs):
        expected_retval = 'foo' if method == 'call' else None

        target = {
            "version": kwargs.pop('version', self.rpcapi.BASE_RPC_API_VERSION)
        }
        expected_msg = copy.deepcopy(kwargs)
        if 'share' in expected_msg:
            share = expected_msg['share']
            del expected_msg['share']
            expected_msg['share_id'] = share['id']
        if 'access' in expected_msg:
            access = expected_msg['access']
            del expected_msg['access']
            expected_msg['access_id'] = access['id']
            del expected_msg['share_id']
        if 'host' in expected_msg:
            del expected_msg['host']
        if 'snapshot' in expected_msg:
            snapshot = expected_msg['snapshot']
            del expected_msg['snapshot']
            expected_msg['snapshot_id'] = snapshot['id']

        if 'host' in kwargs:
            host = kwargs['host']
        elif 'share_server' in kwargs:
            host = kwargs['share_server']['host']
        else:
            host = kwargs['share']['host']
        target['server'] = host
        target['topic'] = '%s.%s' % (CONF.share_topic, host)

        self.fake_args = None
        self.fake_kwargs = None

        def _fake_prepare_method(*args, **kwds):
            for kwd in kwds:
                self.assertEqual(kwds[kwd], target[kwd])
            return self.rpcapi.client

        def _fake_rpc_method(*args, **kwargs):
            self.fake_args = args
            self.fake_kwargs = kwargs
            if expected_retval:
                return expected_retval

        self.stubs.Set(self.rpcapi.client, "prepare", _fake_prepare_method)
        self.stubs.Set(self.rpcapi.client, rpc_method, _fake_rpc_method)

        retval = getattr(self.rpcapi, method)(self.ctxt, **kwargs)

        self.assertEqual(retval, expected_retval)
        expected_args = [self.ctxt, method]
        for arg, expected_arg in zip(self.fake_args, expected_args):
            self.assertEqual(arg, expected_arg)

        for kwarg, value in six.iteritems(self.fake_kwargs):
            self.assertEqual(value, expected_msg[kwarg])

    def test_create_share(self):
        self._test_share_api('create_share',
                             rpc_method='cast',
                             share=self.fake_share,
                             host='fake_host1',
                             snapshot_id='fake_snapshot_id',
                             filter_properties=None,
                             request_spec=None)

    def test_delete_share(self):
        self._test_share_api('delete_share',
                             rpc_method='cast',
                             share=self.fake_share)

    def test_allow_access(self):
        self._test_share_api('allow_access',
                             rpc_method='cast',
                             share=self.fake_share,
                             access=self.fake_access)

    def test_deny_access(self):
        self._test_share_api('deny_access',
                             rpc_method='cast',
                             share=self.fake_share,
                             access=self.fake_access)

    def test_create_snapshot(self):
        self._test_share_api('create_snapshot',
                             rpc_method='cast',
                             share=self.fake_share,
                             snapshot=self.fake_snapshot)

    def test_delete_snapshot(self):
        self._test_share_api('delete_snapshot',
                             rpc_method='cast',
                             snapshot=self.fake_snapshot,
                             host='fake_host')

    def test_delete_share_server(self):
        self._test_share_api('delete_share_server',
                             rpc_method='cast',
                             share_server=self.fake_share_server)
