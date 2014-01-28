# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2013, Nachi Ueno, NTT I3, Inc.
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
import mock
from webob import exc as wexc

from neutron.openstack.common import uuidutils
from neutron.plugins.common import constants
from neutron.services.vpn.device_drivers import cisco_ipsec as ipsec_driver
from neutron.tests import base

_uuid = uuidutils.generate_uuid
FAKE_HOST = 'fake_host'
FAKE_ROUTER_ID = _uuid()
FAKE_VPN_SERVICE = {
    'id': _uuid(),
    'router_id': FAKE_ROUTER_ID,
    'admin_state_up': True,
    'status': constants.PENDING_CREATE,
    'subnet': {'cidr': '10.0.0.0/24'},
    'ipsec_site_connections': [
        {'peer_cidrs': ['20.0.0.0/24',
                        '30.0.0.0/24']},
        {'peer_cidrs': ['40.0.0.0/24',
                        '50.0.0.0/24']}]
}

CSR_REST_CLIENT = ('neutron.services.vpn.device_drivers.'
                   'cisco_csr_rest_client.CsrRestClient')


class TestIPsecDeviceDriver(base.BaseTestCase):
    def setUp(self, driver=ipsec_driver.CiscoCsrIPsecDriver):
        super(TestIPsecDeviceDriver, self).setUp()
        self.addCleanup(mock.patch.stopall)
        for klass in ['neutron.openstack.common.rpc.create_connection',
                      'neutron.context.get_admin_context_without_session',
                      'neutron.openstack.common.'
                      'loopingcall.FixedIntervalLoopingCall']:
            mock.patch(klass).start()
        mock.patch(CSR_REST_CLIENT, autospec=True).start()
        self.agent = mock.Mock()
        self.driver = ipsec_driver.CiscoCsrIPsecDriver(self.agent, FAKE_HOST)
        self.driver.agent_rpc = mock.Mock()
        self.driver.csr.status = 201  # All calls succeed
        self.conn_info = {
            'site_conn': {'id': '123',
                          'psk': 'secret',
                          'peer_address': '192.168.1.2',
                          'peer_cidrs': ['10.1.0.0/24', '10.2.0.0/24'],
                          'mtu': 1500},
            'ike_policy': {'auth_algorithm': 'sha1',
                           'encryption_algorithm': 'aes-128',
                           'pfs': 'Group5',
                           'ike_version': 'v1',
                           'lifetime': {'units': 'seconds',
                                        'value': 3600}},
            'ipsec_policy': {'transform_protocol': 'ah',
                             'encryption_algorithm': 'aes-128',
                             'auth_algorithm': 'sha1',
                             'pfs': 'group5',
                             'lifetime': {'units': 'seconds',
                                          'value': 3600}},
            'cisco': {'site_conn_id': 'Tunnel0',
                      'ike_policy_id': 222,
                      'ipsec_policy_id': 333}
        }

    def test_create_ipsec_site_connection(self):
        """Ensure all steps are done to create an IPSec site connection.

        Verify that each of the driver calls occur (in order), and
        the right information is stored for later deletion.
        """
        expected = ['create_pre_shared_key',
                    'create_ike_policy',
                    'create_ipsec_policy',
                    'create_ipsec_connection',
                    'make_route_id',
                    'create_static_route',
                    'make_route_id',
                    'create_static_route']
        expected_rollback_steps = [
            ipsec_driver.RollbackStep(action='pre_shared_key',
                                      resource_id='123',
                                      title='Pre-Shared Key'),
            ipsec_driver.RollbackStep(action='ike_policy',
                                      resource_id=222,
                                      title='IKE Policy'),
            ipsec_driver.RollbackStep(action='ipsec_policy',
                                      resource_id=333,
                                      title='IPSec Policy'),
            ipsec_driver.RollbackStep(action='ipsec_connection',
                                      resource_id='Tunnel0',
                                      title='IPSec Connection'),
            ipsec_driver.RollbackStep(action='static_route',
                                      resource_id='10.1.0.0_24_Tunnel0',
                                      title='Static Route'),
            ipsec_driver.RollbackStep(action='static_route',
                                      resource_id='10.2.0.0_24_Tunnel0',
                                      title='Static Route')]
        self.driver.csr.make_route_id.side_effect = ['10.1.0.0_24_Tunnel0',
                                                     '10.2.0.0_24_Tunnel0']
        context = mock.Mock()
        self.driver.create_ipsec_site_connection(context, self.conn_info)
        client_calls = [c[0] for c in self.driver.csr.method_calls]
        self.assertEqual(expected, client_calls)
        self.assertEqual(
            expected_rollback_steps,
            self.driver.connections[self.conn_info['site_conn']['id']])

    def test_create_ipsec_site_connection_with_rollback(self):
        """Failure test of IPSec site conn creation that fails and rolls back.

        Simulate a failure in the last create step (making routes for the
        peer networks), and ensure that the create steps are called in
        order (except for create_static_route), and that the delete
        steps are called in reverse order. At the end, there should be no
        rollback infromation for the connection.
        """
        expected = ['create_pre_shared_key',
                    'create_ike_policy',
                    'create_ipsec_policy',
                    'create_ipsec_connection',
                    'delete_ipsec_connection',
                    'delete_ipsec_policy',
                    'delete_ike_policy',
                    'delete_pre_shared_key']
        del self.conn_info['site_conn']['peer_cidrs']
        context = mock.Mock()
        self.driver.create_ipsec_site_connection(context, self.conn_info)
        client_calls = [c[0] for c in self.driver.csr.method_calls]
        self.assertEqual(expected, client_calls)
        self.assertNotIn('123', self.driver.connections)

    def test_create_verification_with_error(self):
        """Negative test of create check step had failed."""
        self.driver.csr.status = wexc.HTTPNotFound.code
        self.assertRaises(ipsec_driver.CsrResourceCreateFailure,
                          self.driver._check_create, 'name', 'id')

    def test_failure_with_invalid_create_step(self):
        """Negative test of invalid create step (programming error)."""
        self.driver.steps = []
        try:
            self.driver.do_create_action('bogus', None, '123', 'Bogus Step')
        except ipsec_driver.CsrResourceCreateFailure:
            pass
        else:
            self.fail('Expected exception with invalid create step')

    def test_failure_with_invalid_delete_step(self):
        """Negative test of invalid delete step (programming error)."""
        self.driver.steps = [ipsec_driver.RollbackStep(action='bogus',
                                                       resource_id='123',
                                                       title='Bogus Step')]
        try:
            self.driver.do_rollback()
        except ipsec_driver.CsrResourceCreateFailure:
            pass
        else:
            self.fail('Expected exception with invalid delete step')


class TestCsrIPsecDeviceDriverCreateTransforms(base.BaseTestCase):

    """Verifies that config info is prepared/transformed correctly."""

    def setUp(self):
        super(TestCsrIPsecDeviceDriverCreateTransforms, self).setUp()
        self.addCleanup(mock.patch.stopall)
        for klass in ['neutron.openstack.common.rpc.create_connection',
                      'neutron.context.get_admin_context_without_session',
                      'neutron.openstack.common.'
                      'loopingcall.FixedIntervalLoopingCall']:
            mock.patch(klass).start()
        mock.patch(CSR_REST_CLIENT, autospec=True).start()
        self.agent = mock.Mock()
        self.driver = ipsec_driver.CiscoCsrIPsecDriver(self.agent, FAKE_HOST)
        self.driver.agent_rpc = mock.Mock()
        self.conn_info = {
            'site_conn': {'id': '123',
                          'psk': 'secret',
                          'peer_address': '192.168.1.2',
                          'peer_cidrs': ['10.1.0.0/24', '10.2.0.0/24'],
                          'mtu': 1500},
            'ike_policy': {'auth_algorithm': 'sha1',
                           'encryption_algorithm': 'aes-128',
                           'pfs': 'Group5',
                           'ike_version': 'v1',
                           'lifetime': {'units': 'seconds',
                                        'value': 3600}},
            'ipsec_policy': {'transform_protocol': 'ah',
                             'encryption_algorithm': 'aes-128',
                             'auth_algorithm': 'sha1',
                             'pfs': 'group5',
                             'lifetime': {'units': 'seconds',
                                          'value': 3600}},
            'cisco': {'site_conn_id': 'Tunnel0',
                      'ike_policy_id': 222,
                      'ipsec_policy_id': 333}
        }

    def test_invalid_attribute(self):
        """Failure test of unknown attribute - programming error."""
        self.assertRaises(ipsec_driver.CsrDriverImplementationError,
                          self.driver.translate_dialect,
                          'ike_policy', 'bogus', self.conn_info)

    def test_policy_missing_lifetime(self):
        """Failure test of missing lifetime attribute.

        Applies to IKE and IPSec policies.
        """
        del self.conn_info['ike_policy']['lifetime']
        self.assertRaises(ipsec_driver.CsrDriverImplementationError,
                          self.driver.validate_lifetime,
                          'ike_policy', self.conn_info['ike_policy'])

    def test_policy_missing_lifetime_units(self):
        """Failure test of missing lifetime attribute.

        Applies to IKE and IPSec policies.
        """
        del self.conn_info['ike_policy']['lifetime']['units']
        self.assertRaises(ipsec_driver.CsrDriverImplementationError,
                          self.driver.validate_lifetime,
                          'ike_policy', self.conn_info['ike_policy'])

    def test_policy_missing_lifetime_value(self):
        """Failure test of missing lifetime attribute.

        Applies to IKE and IPSec policies.
        """
        del self.conn_info['ike_policy']['lifetime']['value']
        self.assertRaises(ipsec_driver.CsrDriverImplementationError,
                          self.driver.validate_lifetime,
                          'ike_policy', self.conn_info['ike_policy'])

    def test_unsupported_lifetime_units(self):
        """Failure test of non-seconds units for lifetime.

        Applies to IKE and IPSec policies.
        """
        self.conn_info['ike_policy']['lifetime']['units'] = 'kilobytes'
        self.assertRaises(ipsec_driver.CsrValidationFailure,
                          self.driver.validate_lifetime,
                          'ike_policy', self.conn_info['ike_policy'])

    def test_valid_lifetime_seconds_values(self):
        """Negative test of unsupported lifetime values for IKE/IPSec."""
        policy_info = {'lifetime': {'units': 'seconds', 'value': 60}}
        actual = self.driver.validate_lifetime('ike_policy', policy_info)
        self.assertEqual(60, actual)
        policy_info = {'lifetime': {'units': 'seconds', 'value': 86400}}
        actual = self.driver.validate_lifetime('ike_policy', policy_info)
        self.assertEqual(86400, actual)
        policy_info = {'lifetime': {'units': 'seconds', 'value': 120}}
        actual = self.driver.validate_lifetime('ipsec_policy', policy_info)
        self.assertEqual(120, actual)
        policy_info = {'lifetime': {'units': 'seconds', 'value': 2592000}}
        actual = self.driver.validate_lifetime('ipsec_policy', policy_info)
        self.assertEqual(2592000, actual)

    def test_unsuported_lifetime_seconds_values(self):
        """Negative test of unsupported lifetime values for IKE/IPSec."""
        self.conn_info['ike_policy']['lifetime']['value'] = 59
        self.assertRaises(ipsec_driver.CsrValidationFailure,
                          self.driver.validate_lifetime,
                          'ike_policy', self.conn_info['ike_policy'])
        self.conn_info['ike_policy']['lifetime']['value'] = 86401
        self.assertRaises(ipsec_driver.CsrValidationFailure,
                          self.driver.validate_lifetime,
                          'ike_policy', self.conn_info['ike_policy'])
        self.conn_info['ipsec_policy']['lifetime']['value'] = 119
        self.assertRaises(ipsec_driver.CsrValidationFailure,
                          self.driver.validate_lifetime,
                          'ipsec_policy', self.conn_info['ipsec_policy'])
        self.conn_info['ipsec_policy']['lifetime']['value'] = 2592001
        self.assertRaises(ipsec_driver.CsrValidationFailure,
                          self.driver.validate_lifetime,
                          'ipsec_policy', self.conn_info['ipsec_policy'])

    def test_psk_create_info(self):
        expected = {u'keyring-name': '123',
                    u'pre-shared-key-list': [
                        {u'key': 'secret',
                         u'encrypted': False,
                         u'peer-address': '192.168.1.2'}]}
        psk_id = self.conn_info['site_conn']['id']
        psk_info = self.driver.create_psk_info(psk_id, self.conn_info)
        self.assertEqual(expected, psk_info)

    def test_create_ike_policy_info(self):
        expected = {u'priority-id': 222,
                    u'encryption': u'aes',
                    u'hash': u'sha',
                    u'dhGroup': 5,
                    u'version': u'v1',
                    u'lifetime': 3600}
        policy_id = self.conn_info['cisco']['ike_policy_id']
        policy_info = self.driver.create_ike_policy_info(policy_id,
                                                         self.conn_info)
        self.assertEqual(expected, policy_info)

    def test_create_ike_policy_info_non_defaults(self):
        self.conn_info['ike_policy'] = {
            'auth_algorithm': 'sha1',
            'encryption_algorithm': 'aes-256',
            'pfs': 'Group14',
            'ike_version': 'v1',
            'lifetime': {'units': 'seconds',
                         'value': 60}
        }
        expected = {u'priority-id': 222,
                    u'encryption': u'aes',  # TODO(pcm): fix
                    u'hash': u'sha',
                    u'dhGroup': 14,
                    u'version': u'v1',
                    u'lifetime': 60}
        policy_id = self.conn_info['cisco']['ike_policy_id']
        policy_info = self.driver.create_ike_policy_info(policy_id,
                                                         self.conn_info)
        self.assertEqual(expected, policy_info)

    def test_failed_create_ike_policy_info_unsupported_version(self):
        """Negative test of unsupported version for IKE policy."""
        self.conn_info['ike_policy']['ike_version'] = 'v2'
        policy_id = self.conn_info['cisco']['ike_policy_id']
        self.assertRaises(ipsec_driver.CsrValidationFailure,
                          self.driver.create_ike_policy_info,
                          policy_id, self.conn_info)

    def test_ipsec_policy_info(self):
        expected = {u'policy-id': 333,
                    u'protection-suite': {
                        u'esp-encryption': u'esp-aes',
                        u'esp-authentication': u'esp-sha-hmac',
                        u'ah': u'ah-sha-hmac'
                    },
                    u'lifetime-sec': 3600,
                    u'pfs': u'group5',
                    u'anti-replay-window-size': u'128'}
        ipsec_policy_id = self.conn_info['cisco']['ipsec_policy_id']
        policy_info = self.driver.create_ipsec_policy_info(ipsec_policy_id,
                                                           self.conn_info)
        self.assertEqual(expected, policy_info)

    def test_ipsec_policy_info_non_defaults(self):
        self.conn_info['ipsec_policy'] = {'transform_protocol': 'ah',
                                          'encryption_algorithm': '3des',
                                          'auth_algorithm': 'sha1',
                                          'pfs': 'group14',
                                          'lifetime': {'units': 'seconds',
                                                       'value': 120}}
        expected = {u'policy-id': 333,
                    u'protection-suite': {
                        u'esp-encryption': u'esp-3des',
                        u'esp-authentication': u'esp-sha-hmac',
                        u'ah': u'ah-sha-hmac'
                    },
                    u'lifetime-sec': 120,
                    u'pfs': u'group14',
                    u'anti-replay-window-size': u'128'}
        ipsec_policy_id = self.conn_info['cisco']['ipsec_policy_id']
        policy_info = self.driver.create_ipsec_policy_info(ipsec_policy_id,
                                                           self.conn_info)
        self.assertEqual(expected, policy_info)

    def test_ipsec_policy_info_protection_suite(self):
        """Try other variations on protection suite settings"""
        pass

    def test_site_connection_info(self):
        expected = {u'vpn-interface-name': 'Tunnel0',
                    u'ipsec-policy-id': 333,
                    u'local-device': {
                        u'ip-address': u'unnumbered GigabitEthernet3',
                        u'tunnel-ip-address': u'172.24.4.23'
                    },
                    u'remote-device': {
                        u'tunnel-ip-address': '192.168.1.2'
                    },
                    u'mtu': 1500}
        ipsec_policy_id = self.conn_info['cisco']['ipsec_policy_id']
        site_conn_id = self.conn_info['cisco']['site_conn_id']
        conn_info = self.driver.create_site_connection_info(site_conn_id,
                                                            ipsec_policy_id,
                                                            self.conn_info)
        self.assertEqual(expected, conn_info)

    def test_static_route_info(self):
        expected = {u'destination-network': '10.2.0.0/24',
                    u'outgoing-interface': 'Tunnel0'}
        route_info = self.driver.create_route_info('10.2.0.0/24', 'Tunnel0')
        self.assertEqual(expected, route_info)


#     def test_vpnservice_updated(self):
#         with mock.patch.object(self.driver, 'sync') as sync:
#             context = mock.Mock()
#             self.driver.vpnservice_updated(context)
#             sync.assert_called_once_with(context, [])

#     def test_create_router(self):
#         process_id = _uuid()
#         process = mock.Mock()
#         process.vpnservice = FAKE_VPN_SERVICE
#         self.driver.processes = {
#             process_id: process}
#         self.driver.create_router(process_id)
#         process.enable.assert_called_once_with()
#
#     def test_destroy_router(self):
#         process_id = _uuid()
#         process = mock.Mock()
#         process.vpnservice = FAKE_VPN_SERVICE
#         self.driver.processes = {
#             process_id: process}
#         self.driver.destroy_router(process_id)
#         process.disable.assert_called_once_with()
#         self.assertNotIn(process_id, self.driver.processes)
#
#     def test_sync_added(self):
#         self.driver.agent_rpc.get_vpn_services_on_host.return_value = [
#             FAKE_VPN_SERVICE]
#         context = mock.Mock()
#         process = mock.Mock()
#         process.vpnservice = FAKE_VPN_SERVICE
#         process.connection_status = {}
#         process.status = constants.ACTIVE
#         process.updated_pending_status = True
#         self.driver.process_status_cache = {}
#         self.driver.processes = {
#             FAKE_ROUTER_ID: process}
#         self.driver.sync(context, [])
#         self.agent.assert_has_calls([
#             mock.call.add_nat_rule(
#                 FAKE_ROUTER_ID,
#                 'POSTROUTING',
#                 '-s 10.0.0.0/24 -d 20.0.0.0/24 -m policy '
#                 '--dir out --pol ipsec -j ACCEPT ',
#                 top=True),
#             mock.call.add_nat_rule(
#                 FAKE_ROUTER_ID,
#                 'POSTROUTING',
#                 '-s 10.0.0.0/24 -d 30.0.0.0/24 -m policy '
#                 '--dir out --pol ipsec -j ACCEPT ',
#                 top=True),
#             mock.call.add_nat_rule(
#                 FAKE_ROUTER_ID,
#                 'POSTROUTING',
#                 '-s 10.0.0.0/24 -d 40.0.0.0/24 -m policy '
#                 '--dir out --pol ipsec -j ACCEPT ',
#                 top=True),
#             mock.call.add_nat_rule(
#                 FAKE_ROUTER_ID,
#                 'POSTROUTING',
#                 '-s 10.0.0.0/24 -d 50.0.0.0/24 -m policy '
#                 '--dir out --pol ipsec -j ACCEPT ',
#                 top=True),
#             mock.call.iptables_apply(FAKE_ROUTER_ID)
#         ])
#         process.update.assert_called_once_with()
#         self.driver.agent_rpc.update_status.assert_called_once_with(
#             context,
#             [{'status': 'ACTIVE',
#              'ipsec_site_connections': {},
#              'updated_pending_status': True,
#              'id': FAKE_VPN_SERVICE['id']}])
#
#     def test_sync_removed(self):
#         self.driver.agent_rpc.get_vpn_services_on_host.return_value = []
#         context = mock.Mock()
#         process_id = _uuid()
#         process = mock.Mock()
#         process.vpnservice = FAKE_VPN_SERVICE
#         self.driver.processes = {
#             process_id: process}
#         self.driver.sync(context, [])
#         process.disable.assert_called_once_with()
#         self.assertNotIn(process_id, self.driver.processes)
#
#     def test_sync_removed_router(self):
#         self.driver.agent_rpc.get_vpn_services_on_host.return_value = []
#         context = mock.Mock()
#         process_id = _uuid()
#         self.driver.sync(context, [{'id': process_id}])
#         self.assertNotIn(process_id, self.driver.processes)