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

from oslo.config import cfg
from sqlalchemy.orm import joinedload

from neutron.common import constants
from neutron.db import agents_db
from neutron.db import l3_agentschedulers_db as l3agentsched_db
from neutron.openstack.common import log as logging
from neutron.openstack.common import timeutils
from neutron.plugins.cisco.l3.common import constants as cl3_constants
from neutron.plugins.cisco.l3.db import l3_models

LOG = logging.getLogger(__name__)


COMPOSITE_AGENTS_SCHEDULER_OPTS = [
    cfg.IntOpt('cfg_agent_down_time', default=10,
               help=_('Seconds of no status update until a cfg agent '
                      'is considered down.')),
]

cfg.CONF.register_opts(COMPOSITE_AGENTS_SCHEDULER_OPTS)

class CompositeAgentSchedulerDbMixin(l3agentsched_db.L3AgentSchedulerDbMixin):
    """Mixin class to add agent scheduler extension to db_plugin_base_v2.
    This class also supports Cisco configuration agents."""

    @classmethod
    def is_agent_down(cls, heart_beat_time,
                      timeout=cfg.CONF.cfg_agent_down_time):
        return timeutils.is_older_than(heart_beat_time, timeout)

    def auto_schedule_hosting_devices_on_cfg_agent(self, context, host,
                                                    router_id):
        # There may be routers that have not been scheduled
        # on a hosting device so we try to do that now
        self.host_router(context, router_id)
        if self.router_scheduler:
            return (self.router_scheduler.
                    auto_schedule_hosting_devices_on_cfg_agent(context, host,
                                                               router_id))

    def list_active_sync_routers_on_active_cfg_agent(self, context, host,
                                                     router_id,
                                                     hosting_device_ids=[]):
        agent = self._get_agent_by_type_and_host(
            context, cl3_constants.AGENT_TYPE_CFG, host)

        if not agent.admin_state_up:
            return []
        query = context.session.query(
            l3_models.RouterHostingDeviceBinding.router_id)
        query = query.join(l3_models.HostingDevice)
        query = query.filter(
            l3_models.HostingDevice.cfg_agent_id == agent.id)
        if router_id:
            query = query.filter(
                l3_models.RouterHostingDeviceBinding.router_id == router_id)
        if len(hosting_device_ids) == 1:
            query = query.filter(
                l3_models.RouterHostingDeviceBinding.hosting_device_id ==
                hosting_device_ids[0])
        elif len(hosting_device_ids) > 1:
            query = query.filter(
                l3_models.RouterHostingDeviceBinding.hosting_device_id.in_(
                    hosting_device_ids))
        router_ids = [item[0] for item in query]
        if router_ids:
            return self.get_sync_data_ext(context, router_ids=router_ids,
                                          active=True)
        else:
            return []

    def add_hosting_device_to_cfg_agent(self, context, agent_id,
                                        hosting_device_id):
        #TODO(bobmel): Implement the adding to cfg agent
        pass

    def remove_hosting_device_from_cfg_agent(self, context, agent_id,
                                             hosting_device_id):
        #TODO(bobmel): Implement the removal from cfg agent
        pass

    def list_hosting_devices_on_cfg_agent(self, context, agent_id):
        #TODO(bobmel): Change so it returns correct hosting devices
        return {'hosting_devices': []}

    def list_cfg_agents_for_hosting_device(self, context, hosting_device_id):
        #TODO(bobmel): Change so it returns correct agent
        return {'cfg_agents': []}

    def get_cfg_agents(self, context, active=None, filters=None):
        query = context.session.query(agents_db.Agent)
        query = query.filter(
            agents_db.Agent.agent_type == cl3_constants.AGENT_TYPE_CFG)
        if active is not None:
            query = (query.filter(agents_db.Agent.admin_state_up == active))
        if filters:
            for key, value in filters.iteritems():
                column = getattr(agents_db.Agent, key, None)
                if column:
                    query = query.filter(column.in_(value))
        cfg_agents = query.all()
        if active is not None:
            cfg_agents = [cfg_agent for cfg_agent in cfg_agents
                          if not self.is_agent_down(
                              cfg_agent['heartbeat_timestamp'])]
        return cfg_agents

    def get_cfg_agents_for_hosting_devices(self, context, hosting_device_ids,
                                           admin_state_up=None, active=None):
        if not hosting_device_ids:
            return []
        query = context.session.query(l3_models.HostingDevice)
        if len(hosting_device_ids) > 1:
            query = query.options(joinedload('cfg_agent')).filter(
                l3_models.HostingDevice.id.in_(hosting_device_ids))
        else:
            query = query.options(joinedload('cfg_agent')).filter(
                l3_models.HostingDevice.id == hosting_device_ids[0])
        if admin_state_up is not None:
            query = (query.filter(agents_db.Agent.admin_state_up ==
                                  admin_state_up))
        agents = [hosting_device.cfg_agent for hosting_device in query
                  if hosting_device.cfg_agent is not None]
        if active is not None:
            agents = [agent for agent in agents if not
                      self.is_agent_down(agent['heartbeat_timestamp'])]
        return agents
