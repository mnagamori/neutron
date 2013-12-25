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

N1KV_PLUGIN = 1
OVS_PLUGIN = 2
ML2_PLUGIN = 3

# Hosting device belong to one of the following categories:
VM_CATEGORY = 'VM'
HARDWARE_CATEGORY = 'Hardware'

# Hosting device is of one of the following types.
NETWORK_NODE_HOST = 'NetworkNamespaceNode'
CSR1KV_HOST = 'CSR1kv'
NEXUS3K_HOST = 'Nexus_3k'

# Router type is a new attribute for OsN Router
# It can be set in Create operation then just Read.
# Router type is instead changed by moving the
# router to a hosting device of another type.
CSR_ROUTER_TYPE = 'CSR1kv'
NAMESPACE_ROUTER_TYPE = 'NetworkNamespace'
HARDWARE_ROUTER_TYPE = 'Hardware'

AGENT_TYPE_CFG = 'Cisco cfg agent'
CSR1kv_SSH_NETCONF_PORT = 22

# Topic for Cisco configuration agent
CFG_AGENT = 'cisco_cfg_agent'

# Service VM status
SVM_OK = 'OK'
SVM_ERROR = 'ERROR'
SVM_NON_RESPONSIVE = 'NON_RESPONSIVE'
