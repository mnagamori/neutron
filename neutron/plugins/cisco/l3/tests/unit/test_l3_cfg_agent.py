# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012 Nicira, Inc.
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

import copy

import mock
from oslo.config import cfg
from testtools import matchers

from neutron.agent.common import config as agent_config
from neutron.plugins.cisco.l3.agent import l3_cfg_agent
from neutron.agent import l3_agent
from neutron.agent.linux import interface
from neutron.common import config as base_config
from neutron.common import constants as l3_constants
from neutron.common import exceptions as n_exc
from neutron.openstack.common import uuidutils
from neutron.tests import base


_uuid = uuidutils.generate_uuid
HOSTNAME = 'myhost'
FAKE_ID = _uuid()


class TestBasicRouterOperations(base.BaseTestCase):

    def setUp(self):
        super(TestBasicRouterOperations, self).setUp()
        self.conf = cfg.ConfigOpts()
        self.conf.register_opts(base_config.core_opts)
        self.conf.register_opts(l3_cfg_agent.L3NATAgent.OPTS)
        agent_config.register_root_helper(self.conf)
    #     self.conf.register_opts(interface.OPTS)
    #     self.conf.set_override('router_id', 'fake_id')
    #     self.conf.set_override('interface_driver',
    #                            'quantum.agent.linux.interface.NullDriver')
    #     self.conf.set_override('send_arp_for_ha', 1)
    #     self.conf.root_helper = 'sudo'
    #
    #     self.device_exists_p = mock.patch(
    #         'quantum.agent.linux.ip_lib.device_exists')
    #     self.device_exists = self.device_exists_p.start()
    #
    #     self.utils_exec_p = mock.patch(
    #         'quantum.agent.linux.utils.execute')
    #     self.utils_exec = self.utils_exec_p.start()
    #
    #     self.external_process_p = mock.patch(
    #         'quantum.agent.linux.external_process.ProcessManager')
    #     self.external_process = self.external_process_p.start()
    #
    #     self.send_arp_p = mock.patch(
    #         'quantum.agent.l3_agent.L3NATAgent._send_gratuitous_arp_packet')
    #     self.send_arp = self.send_arp_p.start()
    #
    #     self.dvr_cls_p = mock.patch('quantum.agent.linux.interface.NullDriver')
    #     driver_cls = self.dvr_cls_p.start()
    #     self.mock_driver = mock.MagicMock()
    #     self.mock_driver.DEV_NAME_LEN = (
    #         interface.LinuxInterfaceDriver.DEV_NAME_LEN)
    #     driver_cls.return_value = self.mock_driver
    #
    #     self.ip_cls_p = mock.patch('quantum.agent.linux.ip_lib.IPWrapper')
    #     ip_cls = self.ip_cls_p.start()
    #     self.mock_ip = mock.MagicMock()
    #     ip_cls.return_value = self.mock_ip
    #
    #     self.l3pluginApi_cls_p = mock.patch(
    #         'quantum.agent.l3_agent.L3PluginApi')
    #     l3pluginApi_cls = self.l3pluginApi_cls_p.start()
    #     self.plugin_api = mock.Mock()
    #     l3pluginApi_cls.return_value = self.plugin_api
    #
    #     #self.looping_call_p = mock.patch(
    #     #    'quantum.openstack.common.loopingcall.FixedIntervalLoopingCall')
    #     #self.looping_call_p.start()
    #
    #     self.addCleanup(mock.patch.stopall)

    def test_router_info_create(self):
        id = _uuid()
        fake_router = {}
        ri = l3_cfg_agent.RouterInfo(id, self.conf.root_helper,
                                     self.conf.use_namespaces, fake_router)

        self.assertTrue(ri.router_name().endswith(id))

    def test_router_info_create_with_router(self):
        id = _uuid()
        ex_gw_port = {'id': _uuid(),
                      'network_id': _uuid(),
                      'fixed_ips': [{'ip_address': '19.4.4.4',
                                     'subnet_id': _uuid()}],
                      'subnet': {'cidr': '19.4.4.0/24',
                                 'gateway_ip': '19.4.4.1'}}
        router = {
            'id': _uuid(),
            'enable_snat': True,
            'routes': [],
            'gw_port': ex_gw_port}
        ri = l3_cfg_agent.RouterInfo(id, self.conf.root_helper,
                                 self.conf.use_namespaces, router)
        self.assertTrue(ri.router_name().endswith(id))
        self.assertEqual(ri.router, router)

    def test_agent_create(self):
        l3_cfg_agent.L3NATAgent(HOSTNAME, self.conf)

    # def _test_internal_network_action(self, action):
    #     port_id = _uuid()
    #     router_id = _uuid()
    #     network_id = _uuid()
    #     ri = cfg_agent.RouterInfo(router_id, self.conf.root_helper,
    #                              self.conf.use_namespaces, None)
    #     agent = cfg_agent.L3NATAgent(HOSTNAME, self.conf)
    #     cidr = '99.0.1.9/24'
    #     mac = 'ca:fe:de:ad:be:ef'
    #     interface_name = agent.get_internal_device_name(port_id)
    #
    #     if action == 'add':
    #         self.device_exists.return_value = False
    #         agent.internal_network_added(ri, network_id,
    #                                      port_id, cidr, mac)
    #         self.assertEqual(self.mock_driver.plug.call_count, 1)
    #         self.assertEqual(self.mock_driver.init_l3.call_count, 1)
    #         self.send_arp.assert_called_once_with(ri, interface_name,
    #                                               '99.0.1.9')
    #     elif action == 'remove':
    #         self.device_exists.return_value = True
    #         agent.internal_network_removed(ri, port_id, cidr)
    #         self.assertEqual(self.mock_driver.unplug.call_count, 1)
    #     else:
    #         raise Exception("Invalid action %s" % action)
    #
    # def test_agent_add_internal_network(self):
    #     self._test_internal_network_action('add')
    #
    # # def test_agent_remove_internal_network(self):
    # #     self._test_internal_network_action('remove')
