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

from neutron.common import constants
from neutron.common import utils
from neutron import context as neutron_context
from neutron.extensions import portbindings
from neutron import manager
from neutron.openstack.common import jsonutils
from neutron.openstack.common import log as logging
from neutron.plugins.common import constants as plugin_constants

LOG = logging.getLogger(__name__)


class L3CfgRpcCallbackMixin(object):
    """A mix-in that enable Cisco cfg agent rpc support in
    plugin implementations.
    """

    def cfg_sync_routers(self, context, **kwargs):
        """Sync routers according to filters to a specific Cisco cfg agent.

        @param context: contains user information
        @param kwargs: host, or router_ids
        @return: a list of routers
                 with their hosting devices, interfaces and floating_ips
        """
        router_ids = kwargs.get('router_ids')
        host = kwargs.get('host')
        #TODO(bobmel): Add functionality to process specific HE's
        hd_ids = kwargs.get('hosting_device_ids', [])
        context = neutron_context.get_admin_context()
        l3plugin = manager.NeutronManager.get_service_plugins()[
            plugin_constants.L3_ROUTER_NAT]
        if not l3plugin:
            routers = {}
            LOG.error(_('No plugin for L3 routing registered! Will reply '
                        'to l3 agent with empty router dictionary.'))
        if utils.is_extension_supported(
                l3plugin, constants.L3_AGENT_SCHEDULER_EXT_ALIAS):
            l3plugin.auto_schedule_hosting_devices_on_cfg_agent(context, host,
                                                                router_ids)
            routers = l3plugin.list_active_sync_routers_on_active_cfg_agent(
                context, host, router_ids, hd_ids)
        else:
            routers = {}
        plugin = manager.NeutronManager.get_plugin()
        if utils.is_extension_supported(
            plugin, constants.PORT_BINDING_EXT_ALIAS):
            self._ensure_host_set_on_ports(context, plugin, host, routers)
        LOG.debug(_("Routers returned to cfg agent:\n %s"),
                  jsonutils.dumps(routers, indent=5))
        return routers

    def _ensure_host_set_on_ports(self, context, plugin, host, routers):
        for router in routers:
            LOG.debug(_("Checking router: %(id)s for host: %(host)s"),
                      {'id': router['id'], 'host': host})
            self._ensure_host_set_on_port(context, plugin, host,
                                          router.get('gw_port'))
            for interface in router.get(constants.INTERFACE_KEY, []):
                self._ensure_host_set_on_port(context, plugin, host,
                                              interface)

    def _ensure_host_set_on_port(self, context, plugin, host, port):
        if (port and
            (port.get(portbindings.HOST_ID) != host or
             port.get(portbindings.VIF_TYPE) ==
             portbindings.VIF_TYPE_BINDING_FAILED)):
            plugin.update_port(context, port['id'],
                               {'port': {portbindings.HOST_ID: host}})

    #TODO(bobmel): Check with Hareesh if we need this function
    def get_external_network_id(self, context, **kwargs):
        """Get one external network id for cfg agent.

        cfg agent expects only on external network when it performs
        this query.
        """
        context = neutron_context.get_admin_context()
        plugin = manager.NeutronManager.get_plugin()
        net_id = plugin.get_external_network_id(context)
        LOG.debug(_("External network ID returned to cfg agent: %s"),
                  net_id)
        return net_id

    def report_non_responding_hosting_devices(self, context, **kwargs):
        """Report that a hosting device cannot be contacted.

        @param: context: contains user information
        @param: kwargs: hosting_device_ids: list of non-responding
                                            hosting devices
        @return: -
        """
        hosting_device_ids = kwargs.get('hosting_device_ids', [])
        cfg_agent = kwargs.get('host', None)
        plugin = manager.NeutronManager.get_service_plugins()[
            plugin_constants.L3_ROUTER_NAT]
        plugin.handle_non_responding_hosting_devices(context, cfg_agent,
                                                     hosting_device_ids)
