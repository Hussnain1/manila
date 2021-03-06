# Copyright 2010 OpenStack LLC.
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

import datetime

from manila import exception as exc

FAKE_UUID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
FAKE_UUIDS = {}


def stub_share(id, **kwargs):
    share = {
        'id': id,
        'share_proto': 'FAKEPROTO',
        'export_location': 'fake_location',
        'user_id': 'fakeuser',
        'project_id': 'fakeproject',
        'host': 'fakehost',
        'size': 1,
        'availability_zone': 'fakeaz',
        'status': 'fakestatus',
        'display_name': 'displayname',
        'display_description': 'displaydesc',
        'created_at': datetime.datetime(1, 1, 1, 1, 1, 1),
        'snapshot_id': '2',
        'volume_type_id': '1',
        'share_network_id': None,
        'share_server_id': 'fake_share_server_id',
    }
    share.update(kwargs)
    return share


def stub_snapshot(id, **kwargs):
    snapshot = {
        'id': id,
        'share_id': 'fakeshareid',
        'share_proto': 'fakesnapproto',
        'export_location': 'fakesnaplocation',
        'user_id': 'fakesnapuser',
        'project_id': 'fakesnapproject',
        'host': 'fakesnaphost',
        'share_size': 1,
        'size': 1,
        'status': 'fakesnapstatus',
        'share_name': 'fakesharename',
        'display_name': 'displaysnapname',
        'display_description': 'displaysnapdesc',
        'created_at': datetime.datetime(1, 1, 1, 1, 1, 1),
    }
    snapshot.update(kwargs)
    return snapshot


def stub_share_get(self, context, share_id, **kwargs):
    return stub_share(share_id, **kwargs)


def stub_share_get_notfound(self, context, share_id):
    raise exc.NotFound


def stub_share_delete(self, context, *args, **param):
    pass


def stub_share_update(self, context, *args, **param):
    share = stub_share('1')
    return share


def stub_snapshot_update(self, context, *args, **param):
    share = stub_share('1')
    return share


def stub_share_get_all_by_project(self, context, sort_key=None, sort_dir=None,
                                  search_opts={}):
    return [stub_share_get(self, context, '1')]


def stub_get_all_shares(self, context):
    return [stub_share(100, project_id='fake'),
            stub_share(101, project_id='superfake'),
            stub_share(102, project_id='superduperfake')]


def stub_snapshot_get(self, context, snapshot_id):
    return stub_snapshot(snapshot_id)


def stub_snapshot_get_notfound(self, context, snapshot_id):
    raise exc.NotFound


def stub_snapshot_create(self, context, share, display_name,
                         display_description):
    return stub_snapshot(200,
                         share_id=share['id'],
                         display_name=display_name,
                         display_description=display_description)


def stub_snapshot_delete(self, context, *args, **param):
    pass


def stub_snapshot_get_all_by_project(self, context, search_opts=None,
                                     sort_key=None, sort_dir=None):
    return [stub_snapshot_get(self, context, 2)]
