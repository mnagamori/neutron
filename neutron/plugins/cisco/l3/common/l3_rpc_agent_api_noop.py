# Copyright (c) 2013 OpenStack Foundation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from neutron.common import topics
from neutron.openstack.common import log as logging


LOG = logging.getLogger(__name__)


# This class is used instead of the L3AgentNotifyAPI to effectively
# disable notifications from the l3 base class to the l3 agents.
class L3AgentNotifyAPINoOp(object):
    """API for plugin to notify L3 agent but without actions."""
    BASE_RPC_API_VERSION = '1.0'

    def __init__(self, topic=topics.L3_AGENT):
        pass

    def _notification_host(self, context, method, payload, host):
        """Notify the agent that is hosting the router."""
        pass

    def _agent_notification(self, context, method, routers_ids,
                            operation, data):
        """Notify changed routers to hosting l3 agents."""
        pass

    def _notification(self, context, method, routers_ids, operation, data):
        """Notify all the agents that are hosting the routers."""
        pass

    def _notification_fanout(self, context, method, router_id):
        """Fanout the deleted router to all L3 agents."""
        pass

    def agent_updated(self, context, admin_state_up, host):
        pass

    def router_deleted(self, context, router_id):
        pass

    def routers_updated(self, context, routers, operation=None, data=None):
        pass

    def router_removed_from_agent(self, context, router_id, host):
        pass

    def router_added_to_agent(self, context, routers, host):
        pass

L3AgentNotifyNoOp = L3AgentNotifyAPINoOp()
