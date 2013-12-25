# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2014 Cisco Systems, Inc.  All rights reserved.
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
#
# @author: Hareesh Puthalath, Cisco Systems, Inc.

import mock
import datetime

from oslo.config import cfg

from neutron.openstack.common import uuidutils
from neutron.openstack.common import log as logging
from neutron.tests import base
from neutron.openstack.common import importutils

from neutron.plugins.cisco.l3.agent.hosting_devices_manager import HostingDevicesManager
from neutron.plugins.cisco.l3.agent.router_info import RouterInfo

_uuid = uuidutils.generate_uuid
LOG = logging.getLogger(__name__)


class TestHostingDevice(base.BaseTestCase):

    def setUp(self):
        super(TestHostingDevice, self).setUp()
        #ToDo : Is this needed? self.conf.register_opts(base_config.core_opts)
        self.hdm = HostingDevicesManager()
        self.hdm._is_pingable = mock.MagicMock()
        self.hdm._is_pingable.return_value = True

        self.hosting_entity = {'id': 123,
                               'host_type': 'CSR1kv',
                               'ip_address': '10.0.0.1',
                               'port': '22',
                               'booting_time': 420}
        self.created_at_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        self.hosting_entity['created_at'] = self.created_at_str
        self.router_id = _uuid()
        self.router = {id: self.router_id,
                       'hosting_device': self.hosting_entity}

    def test_hosting_devices_object(self):

        self.assertEquals(self.hdm.backlog_hosting_devices, {})
        self.assertEquals(self.hdm.router_id_hosting_devices, {})
        self.assertEquals(self.hdm._drivers, {})

    def test_set_driver(self):
        ri = RouterInfo(self.router_id, self.router)
        _driver = self.hdm._set_driver(ri)

        klass = importutils.import_class('neutron.plugins.cisco.l3.'
                                 'agent.csr1000v.'
                                 'csr1000v_routing_driver.'
                                 'CSR1000vRoutingDriver')
        self.assertTrue(isinstance(_driver, klass))

    def test_is_hosting_entity_reachable_positive(self):
        self.assertTrue(self.hdm.is_hosting_entity_reachable(self.router_id,
                                                             self.router))

    def test_is_hosting_entity_reachable_negative(self):
        self.assertEquals(len(self.hdm.backlog_hosting_devices), 0)
        self.hosting_entity['created_at'] = self.created_at_str  # Putting it back to str format
        self.hdm._is_pingable.return_value = False

        self.assertFalse(self.hdm._is_pingable('1.2.3.4'))
        self.assertEquals(self.hdm.is_hosting_entity_reachable(
            self.router_id, self.router), False)
        self.assertEquals(len(self.hdm.backlog_hosting_devices), 1)
        self.assertTrue(123 in self.hdm.backlog_hosting_devices.keys())
        self.assertEquals(self.hdm.backlog_hosting_devices[123]['routers'], [self.router_id])

    def test_test_is_hosting_entity_reachable_negative_exisiting_he(self):
        self.hdm.backlog_hosting_devices.clear()
        self.hdm.backlog_hosting_devices[123] = {'he': None, 'routers': [_uuid()]}

        self.assertEquals(len(self.hdm.backlog_hosting_devices), 1)
        self.assertEquals(self.hdm.is_hosting_entity_reachable(
            self.router_id, self.router), False)
        self.assertEquals(len(self.hdm.backlog_hosting_devices), 1)
        self.assertTrue(123 in self.hdm.backlog_hosting_devices.keys())
        self.assertEquals(len(self.hdm.backlog_hosting_devices[123]['routers']), 2)

    def test_check_backlog_empty(self):

        expected = {'reachable': [],
                    'dead': []}

        self.assertEquals(self.hdm.check_backlogged_hosting_entities(), expected)

    def test_check_backlog_below_booting_time(self):
        expected = {'reachable': [],
                    'dead': []}
        created_at_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
        created_at_str_now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")

        self.hosting_entity['created_at'] = created_at_str_now
        he = self.hosting_entity
        he_id = he['id']
        self.hdm.backlog_hosting_devices[he_id] = {'he': he,
                                                    'routers': [self.router_id]}

        self.assertEquals(self.hdm.check_backlogged_hosting_entities(), expected)

        #Simulate after 100 seconds
        timedelta_100 = datetime.timedelta(seconds=100)
        created_at_100sec = datetime.datetime.utcnow() - timedelta_100
        created_at_100sec_str = created_at_100sec.strftime("%Y-%m-%dT%H:%M:%S.%f")

        self.hosting_entity['created_at'] = created_at_100sec_str
        self.assertEquals(self.hdm.check_backlogged_hosting_entities(), expected)

        #Boundary test : 419 seconds : default 420 seconds
        timedelta_419 = datetime.timedelta(seconds=419)
        created_at_419sec = datetime.datetime.utcnow() - timedelta_419
        created_at_419sec_str = created_at_419sec.strftime("%Y-%m-%dT%H:%M:%S.%f")

        self.hosting_entity['created_at'] = created_at_419sec_str
        self.assertEquals(self.hdm.check_backlogged_hosting_entities(), expected)

    def test_check_backlog_above_booting_time_pingable(self):
        # This test simulates a HE which has passed the created time.
        # HE is now pingable.

        #Created time : current time - 420 seconds
        timedelta_420 = datetime.timedelta(seconds=420)
        created_at_420sec = datetime.datetime.utcnow() - timedelta_420
        created_at_420sec_str = created_at_420sec.strftime("%Y-%m-%dT%H:%M:%S.%f")

        self.hosting_entity['created_at'] = created_at_420sec_str
        he = self.hosting_entity
        he_id = he['id']
        self.hdm._is_pingable.return_value = True
        self.hdm.backlog_hosting_devices[he_id] = {'he': he,
                                                    'routers': [self.router_id]}
        expected = {'reachable': [he_id],
                    'dead': []}
        self.assertEquals(self.hdm.check_backlogged_hosting_entities(), expected)

    def test_check_backlog_above_BT_not_pingable_below_deadtime(self):
        #This test simulates a HE which has passed the created time but less than the
        # 'declared dead' time. HE is still not pingable

        #Created time : current time - 420 seconds
        timedelta_420 = datetime.timedelta(seconds=420)
        created_at_420sec = datetime.datetime.utcnow() - timedelta_420
        created_at_420sec_str = created_at_420sec.strftime("%Y-%m-%dT%H:%M:%S.%f")

        he = self.hosting_entity
        he['created_at'] = created_at_420sec_str
        #Inserted in backlog after 60 seconds
        he['backlog_insertion_ts'] = (datetime.datetime.utcnow())
                                      # - datetime.timedelta(seconds=360))

        he_id = he['id']
        self.hdm._is_pingable.return_value = False
        self.hdm.backlog_hosting_devices[he_id] = {'he': he,
                                                    'routers': [self.router_id]}
        expected = {'reachable': [],
                    'dead': []}
        self.assertEquals(self.hdm.check_backlogged_hosting_entities(), expected)

    def test_check_backlog_above_BT_not_pingable_aboveDeadTime(self):
        #This test simulates a HE which has passed the created time but greater than the
        # 'declared dead' time. HE is still not pingable

        #Created time: Current time - 420(Booting time) - 300(Dead time)seconds
        timedelta_720 = datetime.timedelta(seconds=720)
        created_at_720sec = datetime.datetime.utcnow() - timedelta_720
        created_at_720sec_str = created_at_720sec.strftime("%Y-%m-%dT%H:%M:%S.%f")

        he = self.hosting_entity
        he['created_at'] = created_at_720sec_str
        #Inserted in backlog after 60 seconds
        he['backlog_insertion_ts'] = (datetime.datetime.utcnow() -
                                      datetime.timedelta(seconds=420))

        he_id = he['id']
        self.hdm._is_pingable.return_value = False
        self.hdm.backlog_hosting_devices[he_id] = {'he': he,
                                                    'routers': [self.router_id]}
        expected = {'reachable': [],
                    'dead': [he_id]}
        self.assertEquals(self.hdm.check_backlogged_hosting_entities(), expected)
