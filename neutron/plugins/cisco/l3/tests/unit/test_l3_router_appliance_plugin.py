# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2013 Cisco Systems, Inc.  All rights reserved.
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
# @author: Bob Melander, Cisco Systems, Inc.

import mock
from oslo.config import cfg
import webtest

from neutron.api.v2 import attributes
from neutron import context
from neutron.common.test_lib import test_config
from neutron.common import exceptions as n_exc
from neutron.db import api as qdbapi
from neutron.db import db_base_plugin_v2
from neutron.db import model_base
from neutron.extensions import external_net
from neutron.manager import NeutronManager
from neutron.openstack.common import importutils
from neutron.openstack.common import log as logging
from neutron.openstack.common.notifier import api as notifier_api
from neutron.openstack.common.notifier import test_notifier
from neutron.openstack.common import uuidutils
from neutron.plugins.cisco.l3.common import constants as cl3_const
from neutron.plugins.cisco.l3.db import composite_agentschedulers_db as agt_sch_db
from neutron.plugins.cisco.l3.db import hosting_device_manager_db
from neutron.plugins.cisco.l3.db import l3_router_appliance_db
from neutron.plugins.common import constants
from neutron.tests.unit import test_extension_extraroute as test_ext_extraroute
from neutron.tests.unit import test_l3_plugin

LOG = logging.getLogger(__name__)


# This router service plugin class is just for testing
class TestL3RouterAppliancePlugin(db_base_plugin_v2.CommonDbMixin,
                                  l3_router_appliance_db.
                                  L3_router_appliance_db_mixin):

    supported_extension_aliases = ["router",# "ext-gw-mode",
                                   "extraroute"]

    def __init__(self):
        qdbapi.register_models(base=model_base.BASEV2)
        self.hosting_scheduler = importutils.import_object(
            cfg.CONF.hosting_scheduler_driver)

    @classmethod
    def resetPlugin(cls):
        cls.hosting_scheduler = None

    def get_plugin_type(self):
        return constants.L3_ROUTER_NAT

    def get_plugin_description(self):
        return "Cisco L3 Routing Service Plugin for testing"


# Functions to mock service VM creation.
def dispatch_service_vm_mock(self, context, instance_name, vm_image,
                             vm_flavor, hosting_device_drv, mgmt_port,
                             ports=None):
    vm_id = uuidutils.generate_uuid()

    try:
        # Assumption for now is that this does not need to be
        # plugin dependent, only hosting device type dependent.
        cfg_files = hosting_device_drv.create_configdrive_files(
            context, mgmt_port)
        files = {label: name for label, name in cfg_files.items()}
    except IOError:
        return None

    if mgmt_port is not None:
        p_dict = {'port': {'device_id': vm_id,
                           'device_owner': 'nova'}}
        self._core_plugin.update_port(context, mgmt_port['id'], p_dict)

    for port in ports:
        p_dict = {'port': {'device_id': vm_id,
                           'device_owner': 'nova'}}
        self._core_plugin.update_port(context, port['id'], p_dict)

    myserver = {'server': {'adminPass': "MVk5HPrazHcG",
                'id': vm_id,
                'links': [{'href': "http://openstack.example.com/v2/"
                                    "openstack/servers/" + vm_id,
                           'rel': "self"},
                            {'href': "http://openstack.example.com/"
                                      "openstack/servers/" + vm_id,
                             'rel': "bookmark"}]}}

    return myserver['server']


def delete_service_vm_mock(self, context, vm_id, hosting_device_drv,
                           mgmt_nw_id):
        result = True
        # Get ports on management network (should be only one)
        ports = self._core_plugin.get_ports(
            context, filters={'device_id': [vm_id],
                              'network_id': [mgmt_nw_id]})
        if ports:
            hosting_device_drv.delete_configdrive_files(context, ports[0])

        try:
            ports = self._core_plugin.get_ports(context,
                                                filters={'device_id': [vm_id]})
            for port in ports:
                self._core_plugin.delete_port(context, port['id'])
        except n_exc.NeutronException as e:
            LOG.error(_('Failed to delete service VM %(id)s due to %(err)s'),
                      {'id': vm_id, 'err': e})
            result = False
        return result


def get_hosting_device_template_mock(self, context, host_type):
    return {'id': '11111111-2222-3333-4444-555555555555',
            'tenant_id': self.l3_tenant_id(),
            'name': 'CSR1kv',
            'enabled': True,
            'host_category': cl3_const.VM_CATEGORY,
            'host_type': cl3_const.CSR1KV_HOST,
            'service_types': 'router',
            'image': cfg.CONF.csr1kv_image,
            'flavor': cfg.CONF.csr1kv_flavor,
            'configuration_mechanism': 'Netconf',
            'transport_port': cl3_const.CSR1kv_SSH_NETCONF_PORT,
            'booting_time': cfg.CONF.csr1kv_booting_time,
            'capacities': 'router:' + str(cfg.CONF.max_routers_per_csr1kv),
            'tenant_bound': None,
            'device_driver': 'neutron.plugins.cisco.l3.tests.unit.'
                             'hd_dummy_driver.DummyHostingDeviceDriver',
            'plugging_driver': 'neutron.plugins.cisco.l3.tests.unit.'
                               'plugging_dummy_driver.DummyTrunkingPlugDriver',
            'cfg_agent_driver': 'router:neutron.plugins.cisco.l3.agent.'
                                'csr1000v.cisco_csr_network_driver.'
                                'CiscoCSRDriver',
            'schedulers': 'router:neutron.plugins.cisco.l3.scheduler.XXX.'
                          'YYY'
            }


class L3RouterApplianceTestCase(test_ext_extraroute.ExtraRouteDBSepTestCase):

    def setUp(self):
        # the plugin without L3 support
        plugin = 'neutron.tests.unit.test_l3_plugin.TestNoL3NatPlugin'
        # the L3 service plugin
        l3_plugin = (
            'neutron.plugins.cisco.l3.tests.unit.'
            'test_l3_router_appliance_plugin.TestL3RouterAppliancePlugin')
        service_plugins = {'l3_plugin_name': l3_plugin}

        # for these tests we need to enable overlapping ips
        cfg.CONF.set_default('allow_overlapping_ips', True)
        cfg.CONF.set_default('max_routes', 3)
        ext_mgr = test_ext_extraroute.ExtraRouteTestExtensionManager()

        hosting_device_manager_db.HostingDeviceManager._instance = None
        hosting_device_manager_db.HostingDeviceManager._mgmt_nw_uuid = None
        hosting_device_manager_db.HostingDeviceTemplate._mgmt_sec_grp_id = None

        super(test_l3_plugin.L3BaseForSepTests, self).setUp(
            plugin=plugin, ext_mgr=ext_mgr,
            service_plugins=service_plugins)

        # Set to None to reload the drivers
        notifier_api._drivers = None
        cfg.CONF.set_override("notification_driver", [test_notifier.__name__])

        cfg.CONF.set_override('allow_sorting', True)
        test_opts = [
            cfg.StrOpt('auth_protocol', default='http'),
            cfg.StrOpt('auth_host', default='localhost'),
            cfg.IntOpt('auth_port', default=35357),
            cfg.StrOpt('admin_user', default='neutron'),
            cfg.StrOpt('admin_password', default='secrete')]
        cfg.CONF.register_opts(test_opts, 'keystone_authtoken')

        # Mock l3 admin tenant
        self.tenant_id_fcn_p = mock.patch(
            'neutron.plugins.cisco.l3.db.hosting_device_manager_db.'
            'HostingDeviceManager.l3_tenant_id')
        self.tenant_id_fcn = self.tenant_id_fcn_p.start()
        self.tenant_id_fcn.return_value = "L3AdminTenantId"

        # Mock creation/deletion of service VMs
        self.dispatch_svc_vm_fcn_p = mock.patch(
            'neutron.plugins.cisco.l3.common.service_vm_lib.ServiceVMManager.'
            'dispatch_service_vm', dispatch_service_vm_mock)
        self.dispatch_svc_vm_fcn_p.start()
        self.delete_svc_vm_fcn_p = mock.patch(
            'neutron.plugins.cisco.l3.common.service_vm_lib.ServiceVMManager.'
            'delete_service_vm', delete_service_vm_mock)
        self.delete_svc_vm_fcn_p.start()

        self.get_hosting_device_template_fcn_p = mock.patch(
            'neutron.plugins.cisco.l3.db.hosting_device_manager_db.'
            'HostingDeviceManager.get_hosting_device_template',
            get_hosting_device_template_mock)
        self.get_hosting_device_template_fcn_p.start()

        # A management network/subnet is needed
        self.mgmt_nw = self._make_network(
            self.fmt, cfg.CONF.management_network, True,
            tenant_id="L3AdminTenantId", shared=False)
        self.mgmt_subnet = self._make_subnet(self.fmt, self.mgmt_nw,
                                             "10.0.100.1", "10.0.100.0/24",
                                             ip_version=4)

    def tearDown(self):
        dev_mgr = hosting_device_manager_db.HostingDeviceManager.get_instance()
        dev_mgr.delete_all_service_vm_hosting_devices(
            context.get_admin_context(), cl3_const.CSR1KV_HOST)
        q_p = "network_id=%s" % self.mgmt_nw['network']['id']
        subnets = self._list('subnets', query_params=q_p)
        if subnets:
            for p in self._list('ports', query_params=q_p).get('ports'):
                self._delete('ports', p['id'])
            self._delete('subnets', self.mgmt_subnet['subnet']['id'])
            self._delete('networks', self.mgmt_nw['network']['id'])
        l3_svc_plugin = NeutronManager.get_service_plugins()[
            constants.L3_ROUTER_NAT]
        l3_svc_plugin.resetPlugin()
        self.delete_svc_vm_fcn_p.stop()
        self.dispatch_svc_vm_fcn_p.stop()
        self.tenant_id_fcn_p.stop()
        super(test_l3_plugin.L3NatDBSepTestCase, self).tearDown()

    def test_get_network_succeeds_without_filter(self):
        plugin = NeutronManager.get_plugin()
        dev_mgr = hosting_device_manager_db.HostingDeviceManager.get_instance()
        ctx = context.Context(None, None, is_admin=True)
        result = plugin.get_networks(ctx, filters=None)
        # Remove mgmt network from list
        to_del = -1
        for i in xrange(0, len(result)):
            if result[i].get('id') == dev_mgr.mgmt_nw_id():
                to_del = i
        if to_del != -1:
            del result[to_del]
        self.assertEqual(result, [])

    def test_list_nets_external(self):
        with self.network() as n1:
            self._set_net_external(n1['network']['id'])
            with self.network():
                body = self._list('networks')
                # 3 networks since there is also the mgmt network
                self.assertEqual(len(body['networks']), 3)

                body = self._list(
                    'networks', query_params="%s=True" % external_net.EXTERNAL)
                self.assertEqual(len(body['networks']), 1)

                body = self._list(
                    'networks',
                    query_params="%s=False" % external_net.EXTERNAL)
                # 2 networks since there is also the mgmt network
                self.assertEqual(len(body['networks']), 2)


class L3RouterApplianceTestCaseXML(L3RouterApplianceTestCase):
    fmt = 'xml'


