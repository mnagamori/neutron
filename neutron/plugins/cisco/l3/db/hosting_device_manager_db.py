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

import eventlet
import math
import threading

from oslo.config import cfg
from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy.orm import exc
from sqlalchemy.orm import joinedload
from keystoneclient.v2_0 import client as k_client
from keystoneclient import exceptions as k_exceptions

from neutron.common import utils
from neutron import context as neutron_context
from neutron import manager
from neutron.openstack.common import log as logging
from neutron.openstack.common import importutils
from neutron.openstack.common import timeutils
from neutron.plugins.cisco.l3.common import constants as cl3_const
from neutron.plugins.cisco.l3.common import service_vm_lib
from neutron.plugins.cisco.l3.db.l3_models import HostingDevice
from neutron.plugins.cisco.l3.db.l3_models import HostingDeviceTemplate
from neutron.plugins.cisco.l3.db.l3_models import RouterHostingDeviceBinding

LOG = logging.getLogger(__name__)


HOSTING_DEVICE_MANAGER_OPTS = [
    cfg.StrOpt('l3_admin_tenant', default='L3AdminTenant',
               help=_("Name of the L3 admin tenant")),
    cfg.StrOpt('management_network', default='osn_mgmt_nw',
               help=_("Name of management network for CSR VM configuration")),
    cfg.StrOpt('default_security_group', default='mgmt_sec_grp',
               help=_("Default security group applied on mgmt port")),
    cfg.IntOpt('csr1kv_flavor', default=621,
               help=_("Name or UUID of Nova flavor used for CSR1kv VM")),
    cfg.StrOpt('csr1kv_image', default='csr1kv_openstack_img',
               help=_("Name or UUID of Glance image used for CSR1kv VM")),
    cfg.IntOpt('max_routers_per_csr1kv', default=1,
               help=_("The maximum number of logical routers a CSR1kv VM "
                      "instance should host")),
    cfg.IntOpt('csr1kv_booting_time', default=420,
               help=_("The time in seconds it typically takes to "
                      "boot a CSR1kv VM")),
    cfg.IntOpt('standby_pool_size', default=1,
               help=_("The number of running service VMs to maintain "
                      "as a pool of standby hosting devices")),
]

cfg.CONF.register_opts(HOSTING_DEVICE_MANAGER_OPTS)


class HostingDeviceManager(object):
    """ A class implementing a resource manager for hosting devices.

    The caller should make sure that HostingDeviceManager is a singleton.
    """
    # The one and only instance
    _instance = None
    # The all-mighty tenant owning all hosting devices
    _l3_tenant_uuid = None
    # The management network for hosting devices
    _mgmt_nw_uuid = None
    _mgmt_sec_grp_id = None

    # Dictionaries with loaded driver modules for different host types
    _plugging_drivers = {}
    _hosting_device_drivers = {}

    # Slot counters for resource management
    _slots = {cl3_const.CSR1KV_HOST: {
        # rlock to control thread access
        'lock': threading.RLock(),
        # 'available' only count tenant unbound slots
        'available': -1,
        # number of tenant unbound slots to keep available
        'desired': cfg.CONF.max_routers_per_csr1kv *
        cfg.CONF.standby_pool_size}}

    # Dictionary of hosting device capacities
    _capacities = {}

    def __init__(self):
        auth_url = (cfg.CONF.keystone_authtoken.auth_protocol + "://" +
                    cfg.CONF.keystone_authtoken.auth_host + ":" +
                    str(cfg.CONF.keystone_authtoken.auth_port) + "/v2.0")
        u_name = cfg.CONF.keystone_authtoken.admin_user
        pw = cfg.CONF.keystone_authtoken.admin_password
        tenant = cfg.CONF.l3_admin_tenant
        self._svc_vm_mgr = service_vm_lib.ServiceVMManager(
            user=u_name, passwd=pw, l3_admin_tenant=tenant, auth_url=auth_url)
        self._gt_pool = eventlet.GreenPool()
        #TODO(bobmel): determine host_types from DB
        templates = [{'host_type': cl3_const.CSR1KV_HOST,
                      'host_category': cl3_const.VM_CATEGORY}]
        for item in templates:
            self.report_hosting_device_shortage(
                neutron_context.get_admin_context(), item['host_type'],
                item['host_category'])

    @classmethod
    def get_instance(cls):
        # double checked locking
        if cls._instance is None:
            cls._create_instance()
        return cls._instance

    @classmethod
    def l3_tenant_id(cls):
        if cls._l3_tenant_uuid is None:
            auth_url = (cfg.CONF.keystone_authtoken.auth_protocol + "://" +
                        cfg.CONF.keystone_authtoken.auth_host + ":" +
                        str(cfg.CONF.keystone_authtoken.auth_port) + "/v2.0")
            user = cfg.CONF.keystone_authtoken.admin_user
            pw = cfg.CONF.keystone_authtoken.admin_password
            tenant = cfg.CONF.keystone_authtoken.admin_tenant_name
            keystone = k_client.Client(username=user, password=pw,
                                       tenant_name=tenant,
                                       auth_url=auth_url)
            try:
                tenant = keystone.tenants.find(name=cfg.CONF.l3_admin_tenant)
                cls._l3_tenant_uuid = tenant.id
            except k_exceptions.NotFound:
                LOG.error(_('No tenant with a name or ID of %s exists.'),
                            cfg.CONF.l3_admin_tenant)
            except k_exceptions.NoUniqueMatch:
                LOG.error(_('Multiple tenants matches found for %s'),
                            cfg.CONF.l3_admin_tenant)
        return cls._l3_tenant_uuid

    @classmethod
    def mgmt_nw_id(cls):
        if cls._mgmt_nw_uuid is None:
            tenant_id = cls.l3_tenant_id()
            if not tenant_id:
                return None
            net = manager.NeutronManager.get_plugin().get_networks(
                neutron_context.get_admin_context(),
                {'tenant_id': [tenant_id],
                 'name': [cfg.CONF.management_network]},
                ['id', 'subnets'])
            if len(net) == 1:
                num_subnets = len(net[0]['subnets'])
                if num_subnets == 0:
                    LOG.error(_('The management network has no subnet. '
                                'Please assign one.'))
                    return
                elif num_subnets > 1:
                     LOG.info(_('The management network has %s subnets. The '
                                'first one will be used.'),
                              num_subnets)
                cls._mgmt_nw_uuid = net[0].get('id')
            elif len(net) > 1:
                # Management network must have a unique name.
                LOG.error(_('The management network for does not have unique '
                            'name. Please ensure that it is.'))
            else:
                # Management network has not been created.
                LOG.error(_('There is no virtual management network. Please '
                            'create one.'))
        return cls._mgmt_nw_uuid

    @classmethod
    def mgmt_sec_grp_id(cls):
        if not utils.is_extension_supported(cls._core_plugin,
                                            "security-group"):
            return None
        if cls._mgmt_sec_grp_id is None:
            # Get the id for the _mgmt_security_group_id
            tenant_id = cls.l3_tenant_id()
            res = manager.NeutronManager.get_plugin().get_security_groups(
                neutron_context.get_admin_context(),
                {'tenant_id': [tenant_id],
                 'name': [cfg.CONF.default_security_group]},
                ['id'])
            if len(res) == 1:
                sec_grp_id = res[0].get('id', None)
                cls._mgmt_sec_grp_id = sec_grp_id
            elif len(res) > 1:
                # the mgmt sec group must be unique.
                LOG.error(_('The security group for the management network '
                            'does not have unique name. Please ensure that '
                            'it is.'))
            else:
                # CSR Mgmt security group is not present.
                LOG.error(_('There is no security group for the management '
                            'network. Please create one.'))
        return cls._mgmt_sec_grp_id

    def get_hosting_device_driver(self, context, host_type):
        """Returns the driver for hosting device with specified
           hosting_device_id or hosting devices with specified host_type."""
        try:
            return self._hosting_device_drivers[host_type]
        except KeyError:
            try:
                template = (self.get_hosting_device_template(
                    context, host_type) or {})
                self._hosting_device_drivers[host_type] = (
                    importutils.import_object(template.get('device_driver')))
            except ImportError:
                LOG.exception(_("Error loading hosting device driver "
                                "%(driver)s for host type %(host_type)s"),
                          {'driver': template.get('device_driver'),
                           'host_type': host_type})
                raise
            return self._hosting_device_drivers[host_type]

    def get_hosting_device_plugging_driver(self, context, host_type):
        """Returns the plugging driver for hosting device with specified
           hosting_device_id or hosting devices with specified host_type."""
        try:
            return self._plugging_drivers[host_type]
        except KeyError:
            try:
                template = (self.get_hosting_device_template(
                    context, host_type) or {})
                self._plugging_drivers[host_type] = importutils.import_object(
                    template.get('plugging_driver'))
            except ImportError:
                LOG.exception(_("Error loading plugging driver %(driver)s for "
                                "host type %(host_type)s"),
                              {'driver': template.get('plugging_driver'),
                               'host_type': host_type})
                raise
            return self._plugging_drivers[host_type]

    def get_hosting_device_capacity(self, context, host_type):
        """Returns how many logical resources a hosting device
           of specified host_type can host."""
        try:
            return self._capacities[host_type]
        except KeyError:
            template = (self.get_hosting_device_template(context, host_type) or
                        {})
            capacity = {}
            try:
                for spec in template.get('capacities', "").split(','):
                    resource, num = spec.split(':')
                    capacity['num_' + resource + 's'] = int(num)
            except ValueError:
                LOG.exception(_("Error parsing capacities specification: "
                                "%(capacities)s for host type %(host_type)s"),
                              {'capacities': template.get('capacities'),
                               'host_type': host_type})
                capacity = {}
            if not capacity:
                return
            self._capacities[host_type] = capacity
            return capacity

    def report_hosting_device_shortage(self, context, host_type, category):
        # safety net: synchronize counters to ensure correct values
        if host_type == cl3_const.CSR1KV_HOST:
            self._sync_hosting_device_pool_counters(context, host_type)
            mgr_context = neutron_context.get_admin_context()
            self._gt_pool.spawn_n(self._maintain_hosting_device_pool,
                                  mgr_context, host_type, category)

    def acquire_hosting_device_slot(self, context, router, hosting_device):
        with context.session.begin(subtransactions=True):
            if hosting_device is None:
                return False
            host_type = hosting_device['host_type']
            category = hosting_device['host_category']
            slots = self._get_slots_counters(host_type)
            if slots is None:
                return False
            with slots['lock']:
                if slots['available'] < 0:
                    self._sync_hosting_device_pool_counters(context, host_type)
                if router['share_host']:
                    # For tenant unbound hosting devices we allocate a
                    # single slot available immediately
                    reduce_by = 1
                elif hosting_device['tenant_bound'] is None:
                    # Make hosting device tenant bound and remove all of
                    # its slots from the available pool
                    reduce_by = self.get_hosting_device_capacity(
                        context, host_type)
                    hosting_device[0]['tenant_bound'] = router['id']
                    context.session.add(hosting_device[0])
                else:
                    # Tenant bound slots are all allocated when a
                    # hosting device becomes tenant bound
                    reduce_by = 0
                slots['available'] -= reduce_by
                mgr_context = neutron_context.get_admin_context()
                self._gt_pool.spawn_n(self._maintain_hosting_device_pool,
                                      mgr_context, host_type, category)
        return True

    def release_hosting_device_slot(self, context, hosting_device):
        with context.session.begin(subtransactions=True):
            if hosting_device is None:
                return False
            host_type = hosting_device['host_type']
            category = hosting_device['host_category']
            slots = self._get_slots_counters(host_type)
            if slots is None:
                return False
            with slots['lock']:
                if slots['available'] < 0:
                    self._sync_hosting_device_pool_counters(context, host_type)
                    # Sync calculation includes the unscheduled router so
                    # we must subtract it here to avoid counting it twice.
                    slots['available'] = max(0, slots['available'] - 1)
                if hosting_device['tenant_bound'] is not None:
                    query = context.session.query(RouterHostingDeviceBinding)
                    query = query.filter(
                        RouterHostingDeviceBinding.hosting_device_id ==
                        hosting_device['id'])
                    # Have we removed the last Neutron router hosted on
                    # this (tenant bound) hosting device?
                    if query.count() == 0:
                        # Make hosting device tenant unbound again and
                        # return all its slots to available pool
                        inc_by = self.get_hosting_device_capacity(
                            context, host_type)
                        hosting_device['tenant_bound'] = None
                        context.session.add(hosting_device)
                    else:
                        # We return all slots to available pool when
                        # hosting device becomes tenant unbound
                        inc_by = 0
                else:
                    # For tenant unbound hosting devices we can make
                    # the slot available immediately
                    inc_by = 1
                slots['available'] += inc_by
                mgr_context = neutron_context.get_admin_context()
                self._gt_pool.spawn_n(self._maintain_hosting_device_pool,
                                      mgr_context, host_type, category)
        return True

    def get_hosting_devices(self, context, hosting_device_ids):
        query = context.session.query(HostingDevice)
        if len(hosting_device_ids) > 1:
            query = query.options(joinedload('cfg_agent')).filter(
                HostingDevice.id.in_(hosting_device_ids))
        else:
            query = query.options(joinedload('cfg_agent')).filter(
                HostingDevice.id == hosting_device_ids[0])
        return query.all()

    def get_hosting_device_template(self, context, host_type):
        # TODO(bobmel): This should NOT be hard coded to the CSR1kv
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
                'device_driver': 'neutron.plugins.cisco.l3.hosting_device_'
                                 'drivers.csr1kv_hd_driver.'
                                 'CSR1kvHostingDeviceDriver',
                'plugging_driver': 'neutron.plugins.cisco.l3.plugging_drivers.'
                                   'n1kv_trunking_driver.'
                                   'N1kvTrunkingPlugDriver',
#                                   'ovs_trunking_driver.OvsTrunkingPlugDriver',
                'cfg_agent_driver': 'router:neutron.plugins.cisco.l3.agent.'
                                    'csr1000v.cisco_csr_network_driver.'
                                    'CiscoCSRDriver',
                'schedulers': 'router:neutron.plugins.cisco.l3.scheduler.XXX.'
                              'YYY'
                }
        query = context.session.query(HostingDeviceTemplate)
        query = query.filter(HostingDevice.host_type == host_type)
        try:
            return query.one()
        except exc.MultipleResultsFound as e:
            LOG.debug(_('Multiple hosting device templates with same host '
                        'type %s. Please remove duplicates to ensure '
                        'uniqueness.'), host_type)
            return
        except exc.NoResultFound as e:
            LOG.error(_('No hosting device templates with host type %s '
                        'found.'), host_type)
            return

    def delete_all_service_vm_hosting_devices(self, context, host_type):
        with context.session.begin(subtransactions=True):
            plugging_drv = self.get_hosting_device_plugging_driver(
                context, host_type)
            hosting_device_drv = self.get_hosting_device_driver(
                context, host_type)
            if plugging_drv is None or hosting_device_drv is None:
                return
            query = context.session.query(HostingDevice)
            query = query.filter(HostingDevice.host_type == host_type)
            for hd in query:
                res = plugging_drv.get_hosting_device_resources(
                    context, hd.id, self.l3_tenant_id(), self.mgmt_nw_id())
                self._svc_vm_mgr.delete_service_vm(
                    context, hd.id, hosting_device_drv, self.mgmt_nw_id())
                plugging_drv.delete_hosting_device_resources(
                    context, self.l3_tenant_id(), **res)
                context.session.delete(hd)
            # Adjust pool counters, easiest is to just re-sync.
            self._sync_hosting_device_pool_counters(context, host_type)

    def process_non_responsive_hosting_device(
            self, context, hosting_device, logical_resource_ids):
        """ Host type specific processing of non responsive hosting devices.

        :param hosting_device: db object for hosting device
        :param logical_resource_ids: dict{'routers': [id1, id2, ...]}
        :return: True if hosting_device has been deleted, otherwise False
        """
        if hosting_device['host_category'] == cl3_const.VM_CATEGORY:
            self._delete_dead_service_vm_hosting_device(
                context, hosting_device, logical_resource_ids)
            return True
        return False

    @property
    def _core_plugin(self):
        return manager.NeutronManager.get_plugin()

    @classmethod
    @utils.synchronized("hosting_device_manager")
    def _create_instance(cls):
         if cls._instance is None:
             cls._instance = cls()

    def _get_slots_counters(self, host_type):
        return self._slots.get(host_type)

    def _sync_hosting_device_pool_counters(self, context, host_type):
        # mysql> SELECT COUNT(id) FROM hostingdevices
        # WHERE host_type='CSR1kv' AND tenant_bound IS NULL;
        slots = self._get_slots_counters(host_type)
        capacity = (self.get_hosting_device_capacity(context, host_type) or
                    {}).get('num_routers')
        if slots is None or capacity is None:
            return
        with slots['lock']:
            query = context.session.query(func.count(HostingDevice.id))
            query = query.filter(and_(HostingDevice.host_type == host_type,
                                      HostingDevice.admin_state_up == True,
                                      HostingDevice.tenant_bound == None))
            non_tenant_bound_he = query.scalar()

            #mysql> SELECT hostingdevices.id FROM hostingdevices AS he
            # JOIN routerhostingentitybindings AS rhe
            # ON he.id = rhe.hosting_device_id
            # WHERE he.host_type = 'CSR1kv' AND he.tenant_bound IS NULL

            query = context.session.query(HostingDevice.id).join(
                RouterHostingDeviceBinding,
                HostingDevice.id ==
                RouterHostingDeviceBinding.hosting_device_id)
            query = query.filter(and_(HostingDevice.host_type == host_type,
                                      HostingDevice.admin_state_up == True,
                                      HostingDevice.tenant_bound == None))
            n_used_slots = query.count()
            slots['available'] = capacity * non_tenant_bound_he - n_used_slots

    def _maintain_hosting_device_pool(self, context, host_type, host_category):
        """Ensures that the number of standby hosting devices (essentially
        service VMs) is kept at a suitable level so that resource creation is
        not slowed down by booting of the hosting device.
        """
        # For now the pool size is only elastic for service VMs.
        if host_category == cl3_const.HARDWARE_CATEGORY:
            return
        # Maintain a pool of approximately _desired_svc_vm_slots =
        #     capacity * standby_pool_size
        # slots available for use.
        # Approximately means _avail_svc_vm_slots =
        #         [ _desired_svc_vm_slots - capacity,
        #           _desired_svc_vm_slots - capacity ]
        #
        # Spin-up VM condition: _service_vm_slots < _desired_svc_vm_slots
        # Resulting increase of available slots:
        #     _avail_svc_vm_slots + capacity
        # Delete VM condition: _service_vm_slots < _desired_svc_vm_slots
        # Resulting reduction of available slots:
        #     _avail_svc_vm_slots - capacity
        slots = self._get_slots_counters(host_type)
        capacity = (self.get_hosting_device_capacity(context, host_type) or
                    {}).get('num_routers')
        if slots is None or capacity is None:
            return
        with slots['lock']:
            if slots['available'] <= slots['desired'] - capacity:
                num_req = int(math.ceil((slots['desired'] -
                                         slots['available']) / (1.0*capacity)))
                num_created = len(self._create_svc_vm_hosting_devices(
                    context, num_req, host_type))
                if num_created < num_req:
                    LOG.warn(_('Requested %{n_requested} service VMs but only '
                               '%{n_created} could be created'),
                             {'n_requested': num_req, 'n_created': num_created})
                slots['available'] += (num_created * capacity)
            elif slots['available'] >= slots['desired'] + capacity:
                num_req = int(math.ceil((slots['available'] -
                                         slots['desired']) / (1.0*capacity)))
                num_deleted = self._delete_unused_service_vm_hosting_devices(
                    context, num_req, host_type)
                if num_deleted < num_req:
                    LOG.warn(_('Tried to delete %{n_requested} service VMs '
                               'but only %{n_deleted} could be deleted'),
                             {'n_requested': num_req, 'n_deleted': num_deleted})
                slots['available'] -= (num_deleted * capacity)

    def _create_svc_vm_hosting_devices(self, context, num, host_type,
                                       tenant_bound=None):
        """Creates a number of service VM instances that will act as
        routing service VM. These hosting devices can be bound to
        a certain tenant or for shared use. A list with the created
        hosting device VMs is returned.
        """
        hosting_devices = []
        plugging_drv = self.get_hosting_device_plugging_driver(
            context, host_type)
        hosting_device_drv = self.get_hosting_device_driver(context, host_type)
        capacity = (self.get_hosting_device_capacity(context, host_type) or
                    {}).get('num_routers')
        template = self.get_hosting_device_template(context, host_type)
        if (plugging_drv is None or hosting_device_drv is None or
                capacity is None):
            return hosting_devices
        with context.session.begin(subtransactions=True):
            # These resources are owned by the L3AdminTenant
            birth_date = timeutils.utcnow()
            for i in xrange(0, num):
                res = plugging_drv.create_hosting_device_resources(
                    context, self.l3_tenant_id(), self.mgmt_nw_id(),
                    self.mgmt_sec_grp_id(), capacity)
                if res.get('mgmt_port') is None:
                    # Required ports could not be created
                    return hosting_devices
                mgmt_port=res['mgmt_port']
                hosting_device = self._svc_vm_mgr.dispatch_service_vm(
                    context, template['name'] + '_nrouter', template['image'],
                    template['flavor'], hosting_device_drv, mgmt_port,
                    res.get('ports'))
                if hosting_device is not None:
                    hosting_devices.append(hosting_device)
                    hd_db = HostingDevice(
                        id=hosting_device['id'],
                        tenant_id=self.l3_tenant_id(),
                        admin_state_up=True,
                        host_type=template['host_type'],
                        host_category=template['host_category'],
#                        ip_address='10.0.100.5',
                        ip_address=mgmt_port['fixed_ips'][0]['ip_address'],
                        transport_port=template['transport_port'],
                        cfg_agent_id=None,
                        created_at=birth_date,
                        booting_time=template['booting_time'],
                        status=None,
                        tenant_bound=template['tenant_bound'],
                        auto_delete_on_fail=True)
                    context.session.add(hd_db)
                else:
                    # Fundamental error like could not contact Nova
                    # Cleanup anything we created
                    plugging_drv.delete_hosting_device_resources(
                        context, self.l3_tenant_id(), **res)
                    return hosting_devices
        return hosting_devices

    def _delete_unused_service_vm_hosting_devices(
            self, context, num, host_type, tenant_bound=None):
        """Deletes <num> or less unused service VM instances that act as
        <host_type> hosting devices (for a certain tenant or for shared
        use). The number of deleted service vm instances is returned.
        """
        # Delete the "youngest" hosting devices since they are
        # more likely to not have finished booting
        num_deleted = 0
        plugging_drv = self.get_hosting_device_plugging_driver(
            context, host_type)
        h_dev_drv = self.get_hosting_device_driver(context, host_type)
        if plugging_drv is None or h_dev_drv is None:
            return num_deleted
        query = context.session.query(HostingDevice)
        query = query.outerjoin(
            RouterHostingDeviceBinding,
            HostingDevice.id == RouterHostingDeviceBinding.hosting_device_id)
        query = query.filter(and_(HostingDevice.host_type == host_type,
                                  HostingDevice.admin_state_up == True,
                                  HostingDevice.tenant_bound == None))
        query = query.group_by(HostingDevice.id)
        query = query.having(
            func.count(RouterHostingDeviceBinding.router_id) == 0)
        query = query.order_by(
            HostingDevice.created_at.desc(),
            func.count(RouterHostingDeviceBinding.router_id))
        hd_candidates = query.all()
        num_possible_to_delete = min(len(hd_candidates), num)
        with context.session.begin(subtransactions=True):
            for i in xrange(0, num_possible_to_delete):
                res = plugging_drv.get_hosting_device_resources(
                    context, hd_candidates[i]['id'], self.l3_tenant_id(),
                    self.mgmt_nw_id())
                if self._svc_vm_mgr.delete_service_vm(
                        context, hd_candidates[i]['id'], h_dev_drv,
                        self.mgmt_nw_id()):
                    context.session.delete(hd_candidates[i])
                    plugging_drv.delete_hosting_device_resources(
                        context, self.l3_tenant_id(), **res)
                    num_deleted += 1
        return num_deleted

    def _delete_dead_service_vm_hosting_device(self, context, hosting_device,
                                               logical_resource_ids):
        """ Deletes a presumably dead service VM. This will indirectly
        make all of its hosted resources (i.e., routers) unscheduled."""
        if hosting_device is None:
            return
        host_type = hosting_device['host_type']
        category = hosting_device['host_category']
        plugging_drv = self.get_hosting_device_plugging_driver(
            context, hosting_device['host_type'])
        hosting_device_drv = self.get_hosting_device_driver(
            context, hosting_device['host_type'])
        slots = self._get_slots_counters(host_type)
        capacity = (self.get_hosting_device_capacity(context, host_type) or
                    {}).get('num_routers')
        if (plugging_drv is None or hosting_device_drv is None or
                slots is None or capacity is None):
            return
        with slots['lock']:
            if slots['available'] < 0:
                self._sync_hosting_device_pool_counters(context, host_type)
            if hosting_device['tenant_bound'] is not None:
                # The slots of a tenant bound hosting device have already
                # been subtracted from available slots so no need to subtract
                # again when that dead hosting device is deleted.
                reduce_by = 0
            else:
                # For an unbound (dead) hosting device all its slots (used
                # and unused) should be counted when reducing the number of
                # available slots.
                reduce_by = capacity
            res = plugging_drv.get_hosting_device_resources(
                context, hosting_device['id'], self.l3_tenant_id(),
                self.mgmt_nw_id())
            if not self._svc_vm_mgr.delete_service_vm(
                    context, hosting_device['id'], hosting_device_drv,
                    self.mgmt_nw_id()):
                LOG.error(_('Failed to delete hosting device %s service VM. '
                            'Will un-register it anyway.'),
                          hosting_device['id'])
            plugging_drv.delete_hosting_device_resources(
                context, self.l3_tenant_id(), **res)
            with context.session.begin(subtransactions=True):
                context.session.delete(hosting_device)
            slots['available'] = (max(0, slots['available'] - reduce_by))
            mgr_context = neutron_context.get_admin_context()
            self._gt_pool.spawn_n(self._maintain_hosting_device_pool,
                                  mgr_context, host_type, category)
