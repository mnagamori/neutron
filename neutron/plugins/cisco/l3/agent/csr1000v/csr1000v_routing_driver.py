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
# @author: Hareesh Puthalath, Cisco Systems, Inc.

import logging
import re
import time
import netaddr

from ncclient import manager
import xml.etree.ElementTree as ET
from ciscoconfparse import CiscoConfParse

from neutron.plugins.cisco.l3.agent.services_api import RoutingDriver
from neutron.plugins.cisco.l3.common import n1kv_constants as n1kv_constants

import cisco_csr_snippets as snippets

LOG = logging.getLogger(__name__)


class CSR1000vRoutingDriver(RoutingDriver):
    """CSR1000v Routing Driver"""

    DEV_NAME_LEN = 14

    def __init__(self, csr_host, csr_ssh_port, csr_user, csr_password):
        self._csr_host = csr_host
        self._csr_ssh_port = csr_ssh_port
        self._csr_user = csr_user
        self._csr_password = csr_password
        self._csr_conn = None
        self._intfs_enabled = False

    ###### Public Functions ########

    def router_added(self, ri):
        self._csr_create_vrf(ri)

    def router_removed(self, ri):
        self._csr_remove_vrf(ri)

    def internal_network_added(self, ri, port):
        self._csr_create_subinterface(ri, port)
        if port.get('ha_info') is not None and ri.ha_info['ha:enabled']:
            self._csr_add_ha(ri, port)
        # if ex_gw_port:
        #     self._csr_add_internalnw_nat_rules(ri, port, ex_gw_port)

    def internal_network_removed(self, ri, port):
        # if ex_gw_port:
        #     self._csr_remove_internalnw_nat_rules(ri, [port], ex_gw_port)
        self._csr_remove_subinterface(ri, port)

    def external_gateway_added(self, ri, ex_gw_port):
        self._csr_create_subinterface(ri, ex_gw_port)
        ex_gw_ip = ex_gw_port['subnet']['gateway_ip']
        if ex_gw_ip:
            #Set default route via this network's gateway ip
            self._csr_add_default_route(ri, ex_gw_ip)

        #Apply NAT rules for internal networks
        # if len(ri.internal_ports) > 0:
        #     for port in ri.internal_ports:
        #         self._csr_add_internalnw_nat_rules(ri, port, ex_gw_port)

    def external_gateway_removed(self, ri, ex_gw_port):
        #Remove internal network NAT rules
        # if len(ri.internal_ports) > 0:
        #     self._csr_remove_internalnw_nat_rules(ri, ri.internal_ports,
        #                                           ex_gw_port)

        ex_gw_ip = ex_gw_port['subnet']['gateway_ip']
        if ex_gw_ip:
            #Remove default route via this network's gateway ip
            self._csr_remove_default_route(ri, ex_gw_ip)

        #Finally, remove external network subinterface
        self._csr_remove_subinterface(ri, ex_gw_port)

    def enable_internal_network_NAT(self, ri, port, ex_gw_port):
            self._csr_add_internalnw_nat_rules(ri, port, ex_gw_port)

    def disable_internal_network_NAT(self, ri, port, ex_gw_port):
            self._csr_remove_internalnw_nat_rules(ri, [port], ex_gw_port)

    def floating_ip_added(self, ri, ex_gw_port, floating_ip, fixed_ip):
        self._csr_add_floating_ip(ri, ex_gw_port, floating_ip, fixed_ip)

    def floating_ip_removed(self, ri, ex_gw_port, floating_ip, fixed_ip):
        self._csr_remove_floating_ip(ri, ex_gw_port, floating_ip, fixed_ip)

    def routes_updated(self, ri, action, route):
        self._csr_update_routing_table(ri, action, route)

    def clear_connection(self):
        self._csr_conn = None

    ##### Internal Functions  ####

    def _csr_create_subinterface(self, ri, port):
        vrf_name = self._csr_get_vrf_name(ri)
        ip_cidr = port['ip_cidr']
        netmask = netaddr.IPNetwork(ip_cidr).netmask
        gateway_ip = ip_cidr.split('/')[0]
        subinterface = self._get_interface_name_from_hosting_port(port)
        vlan = self._get_interface_vlan_from_hosting_port(port)
        self.create_subinterface(subinterface, vlan, vrf_name,
                                 gateway_ip, netmask)

    def _csr_remove_subinterface(self, ri, port):
        vrf_name = self._csr_get_vrf_name(ri)
        subinterface = self._get_interface_name_from_hosting_port(port)
        vlan_id = self._get_interface_vlan_from_hosting_port(port)
        ip = port['fixed_ips'][0]['ip_address']
        self.remove_subinterface(subinterface, vlan_id, vrf_name, ip)

    def _csr_add_ha(self, ri, port):
        func_dict = {
            'HSRP': CSR1000vRoutingDriver._csr_add_ha_HSRP,
            'VRRP': CSR1000vRoutingDriver._csr_add_ha_VRRP,
            'GBLP': CSR1000vRoutingDriver._csr_add_ha_GBLP
        }
        #Invoke the right function for the ha type
        func_dict[ri.ha_info['ha:type']](self, ri, port)

    def _csr_add_ha_HSRP(self, ri, port):
        priority = ri.ha_info['priority']
        port_ha_info = port['ha_info']
        group = port_ha_info['group']
        ip = port_ha_info['virtual_port']['fixed_ips'][0]['ip_address']
        if ip and group and priority:
            vrf_name = self._csr_get_vrf_name(ri)
            subinterface = self._get_interface_name_from_hosting_port(port)
            self._set_ha_HSRP(subinterface, vrf_name, priority, group, ip)

    def _csr_add_ha_VRRP(self, ri, port):
        raise NotImplementedError

    def _csr_add_ha_GBLP(self, ri, port):
        raise NotImplementedError

    def _csr_remove_ha(self, ri, port):
        pass

    def _csr_add_internalnw_nat_rules(self, ri, port, ex_port):
        vrf_name = self._csr_get_vrf_name(ri)
        in_vlan = self._get_interface_vlan_from_hosting_port(port)
        acl_no = 'acl_' + str(in_vlan)
        internal_cidr = port['ip_cidr']
        internal_net = netaddr.IPNetwork(internal_cidr).network
        netmask = netaddr.IPNetwork(internal_cidr).hostmask
        inner_intfc = self._get_interface_name_from_hosting_port(port)
        outer_intfc = self._get_interface_name_from_hosting_port(ex_port)
        self.nat_rules_for_internet_access(acl_no, internal_net,
                                           netmask, inner_intfc,
                                           outer_intfc, vrf_name)

    def _csr_remove_internalnw_nat_rules(self, ri, ports, ex_port):
        acls = []
        #First disable nat in all inner ports
        for port in ports:
            in_intfc_name = self._get_interface_name_from_hosting_port(port)
            inner_vlan = self._get_interface_vlan_from_hosting_port(port)
            acls.append("acl_" + str(inner_vlan))
            self.remove_interface_nat(in_intfc_name, 'inside')

        #Wait for two second
        LOG.debug(_("Sleep for 2 seconds before clearing NAT rules"))
        time.sleep(2)

        #Clear the NAT translation table
        self.remove_dyn_nat_translations()

        # Remove dynamic NAT rules and ACLs
        vrf_name = self._csr_get_vrf_name(ri)
        ext_intfc_name = self._get_interface_name_from_hosting_port(ex_port)
        for acl in acls:
            self.remove_dyn_nat_rule(acl, ext_intfc_name, vrf_name)

    def _csr_add_default_route(self, ri, gw_ip):
        vrf_name = self._csr_get_vrf_name(ri)
        self.add_default_static_route(gw_ip, vrf_name)

    def _csr_remove_default_route(self, ri, gw_ip):
        vrf_name = self._csr_get_vrf_name(ri)
        self.remove_default_static_route(gw_ip, vrf_name)

    def _csr_add_floating_ip(self, ri, ex_gw_port, floating_ip, fixed_ip):
        vrf_name = self._csr_get_vrf_name(ri)
        self.add_floating_ip(floating_ip, fixed_ip, vrf_name)

    def _csr_remove_floating_ip(self, ri, ex_gw_port, floating_ip, fixed_ip):
        vrf_name = self._csr_get_vrf_name(ri)
        out_intfc_name = self._get_interface_name_from_hosting_port(ex_gw_port)
        # First remove NAT from outer interface
        self.remove_interface_nat(out_intfc_name, 'outside')
        #Clear the NAT translation table
        self.remove_dyn_nat_translations()
        #Remove the floating ip
        self.remove_floating_ip(floating_ip, fixed_ip, vrf_name)
        #Enable NAT on outer interface
        self.add_interface_nat(out_intfc_name, 'outside')

    def _csr_update_routing_table(self, ri, action, route):
        #cmd = ['ip', 'route', operation, 'to', route['destination'],
        #       'via', route['nexthop']]
        vrf_name = self._csr_get_vrf_name(ri)
        destination_net = netaddr.IPNetwork(route['destination'])
        dest = destination_net.network
        dest_mask = destination_net.netmask
        next_hop = route['nexthop']
        if action is 'replace':
            self.add_static_route(dest, dest_mask, next_hop, vrf_name)
        elif action is 'delete':
            self.remove_static_route(dest, dest_mask, next_hop, vrf_name)
        else:
            LOG.error(_('Unknown route command %s'), action)

    def _csr_create_vrf(self, ri):
        vrf_name = self._csr_get_vrf_name(ri)
        self.create_vrf(vrf_name)

    def _csr_remove_vrf(self, ri):
        vrf_name = self._csr_get_vrf_name(ri)
        self.remove_vrf(vrf_name)

    def _csr_get_vrf_name(self, ri):
        return ri.router_name()[:self.DEV_NAME_LEN]

    def _get_connection(self):
        """Make SSH connection to the CSR """
        try:
            if self._csr_conn and self._csr_conn.connected:
                return self._csr_conn
            else:
                self._csr_conn = manager.connect(host=self._csr_host,
                                                 port=self._csr_ssh_port,
                                                 username=self._csr_user,
                                                 password=self._csr_password,
                                                 device_params={'name': "csr"},
                                                 timeout=30)
                #self._csr_conn.async_mode = True
                if not self._intfs_enabled:
                    self._intfs_enabled = self._enable_intfs(self._csr_conn)
            return self._csr_conn
        except Exception:
            LOG.exception(_("Failed connecting to CSR1000v. \n"
                            "Connection Params Host:%(host)s "
                            "Port:%(port)s User:%(user)s Password:%(pass)s"),
                          {'host': self._csr_host, 'port': self._csr_ssh_port,
                           'user': self._csr_user, 'pass': self._csr_password})

    def _get_interface_name_from_hosting_port(self, port):
        vlan = self._get_interface_vlan_from_hosting_port(port)
        int_no = self._get_interface_no_from_hosting_port(port)
        intfc_name = 'GigabitEthernet' + str(int_no) + '.' + str(vlan)
        return intfc_name

    def _get_interface_vlan_from_hosting_port(self, port):
        hosting_info = port['hosting_info']
        vlan = hosting_info['segmentation_id']
        return vlan

    def _get_interface_no_from_hosting_port(self, port):
        _name = port['hosting_info']['hosting_port_name']
        if_type = _name.split(':')[0] + ':'
        if if_type == n1kv_constants.T1_PORT_NAME:
            no = str(int(_name.split(':')[1]) * 2)
        elif if_type == n1kv_constants.T2_PORT_NAME:
            no = str(int(_name.split(':')[1]) * 2 + 1)
        else:
            LOG.error(_('Unknown interface name: %s'), if_type)
        return no

    def _get_interfaces(self):
        """
        :return: List of the interfaces
        """
        ioscfg = self._get_running_config()
        parse = CiscoConfParse(ioscfg)
        intfs_raw = parse.find_lines("^interface GigabitEthernet")
        #['interface GigabitEthernet1', 'interface GigabitEthernet2',
        #  'interface GigabitEthernet0']
        intfs = []
        for line in intfs_raw:
            intf = line.strip().split(' ')[1]
            intfs.append(intf)
        LOG.info("Interfaces:%s" % intfs)
        return intfs

    def _get_interface_ip(self, interface_name):
        """
        Get the ip address for an interface
        :param interface_name:
        :return: ip address as a string
        """
        ioscfg = self._get_running_config()
        parse = CiscoConfParse(ioscfg)
        children = parse.find_children("^interface %s" % interface_name)
        for line in children:
            if 'ip address' in line:
                ip_address = line.strip().split(' ')[2]
                LOG.info("IP Address:%s" % ip_address)
                return ip_address
            else:
                LOG.warn("Cannot find interface:" % interface_name)
                return None

    def _interface_exists(self, interface):
        ioscfg = self._get_running_config()
        parse = CiscoConfParse(ioscfg)
        intfs_raw = parse.find_lines("^interface " + interface)
        if len(intfs_raw) > 0:
            return True
        else:
            return False

    def _enable_intfs(self, conn):
        # CSR1kv, in release 3.11 GigabitEthernet 0 is no longer present.
        # so GigabitEthernet 1 is used as management and 2 up
        # is used for data.
        interfaces = ['GigabitEthernet 2', 'GigabitEthernet 3']
        #interfaces = ['GigabitEthernet 1']
        try:
            for i in interfaces:
                confstr = snippets.ENABLE_INTF % i
                rpc_obj = conn.edit_config(target='running', config=confstr)
                if self._check_response(rpc_obj, 'ENABLE_INTF'):
                    LOG.info("Enabled interface %s " % i)
                    time.sleep(1)
        except Exception:
            return False
        return True

    def _get_vrfs(self):
        """
        :return: A list of vrf names as string
        """
        vrfs = []
        ioscfg = self._get_running_config()
        parse = CiscoConfParse(ioscfg)
        vrfs_raw = parse.find_lines("^ip vrf")
        for line in vrfs_raw:
            #  raw format ['ip vrf <vrf-name>',....]
            vrf_name = line.strip().split(' ')[2]
            vrfs.append(vrf_name)
        LOG.info("VRFs:%s" % vrfs)
        return vrfs

    def _get_capabilities(self):
        conn = self._get_connection()
        capabilities = []
        for c in conn.server_capabilities:
            capabilities.append(c)
        LOG.debug("Server capabilities: %s" % capabilities)
        return capabilities

    def _get_running_config(self):
        conn = self._get_connection()
        config = conn.get_config(source="running")
        if config:
            root = ET.fromstring(config._raw)
            running_config = root[0][0]
            #print running_config.text
            rgx = re.compile("\r*\n+")
            ioscfg = rgx.split(running_config.text)
            return ioscfg

    def _check_acl(self, acl_no, network, netmask):
        exp_cfg_lines = ['ip access-list standard ' + str(acl_no),
                         ' permit ' + str(network) + ' ' + str(netmask)]
        ioscfg = self._get_running_config()
        parse = CiscoConfParse(ioscfg)
        acls_raw = parse.find_children(exp_cfg_lines[0])
        if acls_raw:
            if exp_cfg_lines[1] in acls_raw:
                return True
            else:
                LOG.error("Mismatch in ACL configuration for %s" % acl_no)
                return False
        else:
            LOG.debug("%s is not present in config" % acl_no)
            return False

    def _cfg_exists(self, cfg_str):
        ioscfg = self._get_running_config()
        parse = CiscoConfParse(ioscfg)
        cfg_raw = parse.find_lines("^" + cfg_str)
        LOG.debug("_cfg_exists(): Found lines %s " % cfg_raw)
        if len(cfg_raw) > 0:
            return True
        else:
            return False

    def set_interface(self, name, ip_address, mask):
        conn = self._get_connection()
        confstr = snippets.SET_INTC % (name, ip_address, mask)
        rpc_obj = conn.edit_config(target='running', config=confstr)
        print rpc_obj

    def create_vrf(self, vrf_name):
        try:
            conn = self._get_connection()
            confstr = snippets.CREATE_VRF % vrf_name
            rpc_obj = conn.edit_config(target='running', config=confstr)
            if self._check_response(rpc_obj, 'CREATE_VRF'):
                LOG.info("VRF %s successfully created" % vrf_name)
        except Exception:
            LOG.exception("Failed creating VRF %s" % vrf_name)

    def remove_vrf(self, vrf_name):
        if vrf_name in self._get_vrfs():
            conn = self._get_connection()
            confstr = snippets.REMOVE_VRF % vrf_name
            rpc_obj = conn.edit_config(target='running', config=confstr)
            if self._check_response(rpc_obj, 'REMOVE_VRF'):
                LOG.info("VRF %s removed" % vrf_name)
        else:
            LOG.warning("VRF %s not present" % vrf_name)

    def create_subinterface(self, subinterface, vlan_id, vrf_name, ip, mask):
        if vrf_name not in self._get_vrfs():
            LOG.error("VRF %s not present" % vrf_name)
        confstr = snippets.CREATE_SUBINTERFACE % (subinterface, vlan_id,
                                                  vrf_name, ip, mask)
        self.edit_running_config(confstr, 'CREATE_SUBINTERFACE')

    def remove_subinterface(self, subinterface, vlan_id, vrf_name, ip):
        #Optional : verify this is the correct subinterface
        conn = self._get_connection()
        if self._interface_exists(subinterface):
            confstr = snippets.REMOVE_SUBINTERFACE % (subinterface)
            rpc_obj = conn.edit_config(target='running', config=confstr)
            print self._check_response(rpc_obj, 'REMOVE_SUBINTERFACE')

    def _set_ha_HSRP(self, subinterface, vrf_name, priority, group, ip):
        if vrf_name not in self._get_vrfs():
            LOG.error("VRF %s not present" % vrf_name)
        confstr = snippets.SET_INTC_HSRP % (subinterface, vrf_name, group,
                                            priority, group, ip)
        action = "SET_INTC_HSRP (Group: %s, Priority: % s)" % (group, priority)
        self.edit_running_config(confstr, action)

    def _remove_ha_HSRP(self, subinterface, group):
        confstr = snippets.REMOVE_INTC_HSRP % (subinterface, group)
        action = ("REMOVE_INTC_HSRP (subinterface:%s, Group:%s)"
                  % (subinterface, group))
        self.edit_running_config(confstr, action)

    def _get_interface_cfg(self, interface):
        ioscfg = self._get_running_config()
        parse = CiscoConfParse(ioscfg)
        res = parse.find_children('interface ' + interface)
        return res

    def nat_rules_for_internet_access(self, acl_no, network,
                                      netmask,
                                      inner_intfc,
                                      outer_intfc,
                                      vrf_name):
        conn = self._get_connection()
        # Duplicate ACL creation throws error, so checking
        # it first. Remove it in future as this is not common in production
        acl_present = self._check_acl(acl_no, network, netmask)
        #We acquire a lock on the running config and process the edits
        #as a transaction
        with conn.locked(target='running'):
            if not acl_present:
                confstr = snippets.CREATE_ACL % (acl_no, network, netmask)
                rpc_obj = conn.edit_config(target='running', config=confstr)
                print self._check_response(rpc_obj, 'CREATE_ACL')

            confstr = snippets.SET_DYN_SRC_TRL_INTFC % (acl_no, outer_intfc,
                                                        vrf_name)
            rpc_obj = conn.edit_config(target='running', config=confstr)
            print self._check_response(rpc_obj, 'CREATE_SNAT')

            confstr = snippets.SET_NAT % (inner_intfc, 'inside')
            rpc_obj = conn.edit_config(target='running', config=confstr)
            print self._check_response(rpc_obj, 'SET_NAT')

            confstr = snippets.SET_NAT % (outer_intfc, 'outside')
            rpc_obj = conn.edit_config(target='running', config=confstr)
            print self._check_response(rpc_obj, 'SET_NAT')
        # finally:
        #     conn.unlock(target='running')

    def add_interface_nat(self, intfc_name, type):
        conn = self._get_connection()
        confstr = snippets.SET_NAT % (intfc_name, type)
        rpc_obj = conn.edit_config(target='running', config=confstr)
        print self._check_response(rpc_obj, 'SET_NAT '+type)

    def remove_interface_nat(self, intfc_name, type):
        conn = self._get_connection()
        confstr = snippets.REMOVE_NAT % (intfc_name, type)
        rpc_obj = conn.edit_config(target='running', config=confstr)
        print self._check_response(rpc_obj, 'REMOVE_NAT '+type)

    def remove_dyn_nat_rule(self, acl_no, outer_intfc_name, vrf_name):
        conn = self._get_connection()
        confstr = snippets.SNAT_CFG % (acl_no, outer_intfc_name, vrf_name)
        if self._cfg_exists(confstr):
            confstr = snippets.REMOVE_DYN_SRC_TRL_INTFC % (acl_no,
                                                           outer_intfc_name,
                                                           vrf_name)
            rpc_obj = conn.edit_config(target='running', config=confstr)
            print self._check_response(rpc_obj, 'REMOVE_DYN_SRC_TRL_INTFC')

        confstr = snippets.REMOVE_ACL % acl_no
        rpc_obj = conn.edit_config(target='running', config=confstr)
        print self._check_response(rpc_obj, 'REMOVE_ACL')

    def remove_dyn_nat_translations(self):
        conn = self._get_connection()
        confstr = snippets.CLEAR_DYN_NAT_TRANS
        rpc_obj = conn.get(("subtree", confstr))
        print rpc_obj

    def add_floating_ip(self, floating_ip, fixed_ip, vrf):
        conn = self._get_connection()
        confstr = snippets.SET_STATIC_SRC_TRL % (fixed_ip, floating_ip, vrf)
        rpc_obj = conn.edit_config(target='running', config=confstr)
        print self._check_response(rpc_obj, 'SET_STATIC_SRC_TRL')

    def remove_floating_ip(self, floating_ip, fixed_ip, vrf):
        conn = self._get_connection()
        confstr = snippets.REMOVE_STATIC_SRC_TRL % (fixed_ip, floating_ip, vrf)
        rpc_obj = conn.edit_config(target='running', config=confstr)
        print self._check_response(rpc_obj, 'REMOVE_STATIC_SRC_TRL')

    def _get_floating_ip_cfg(self):
        ioscfg = self._get_running_config()
        parse = CiscoConfParse(ioscfg)
        res = parse.find_lines('ip nat inside source static')
        return res

    def add_static_route(self, dest, dest_mask, next_hop, vrf):
        conn = self._get_connection()
        confstr = snippets.SET_IP_ROUTE % (vrf, dest, dest_mask, next_hop)
        rpc_obj = conn.edit_config(target='running', config=confstr)
        print self._check_response(rpc_obj, 'SET_IP_ROUTE')

    def remove_static_route(self, dest, dest_mask, next_hop, vrf):
        conn = self._get_connection()
        confstr = snippets.REMOVE_IP_ROUTE % (vrf, dest, dest_mask, next_hop)
        rpc_obj = conn.edit_config(target='running', config=confstr)
        print self._check_response(rpc_obj, 'REMOVE_IP_ROUTE')

    def _get_static_route_cfg(self):
        ioscfg = self._get_running_config()
        parse = CiscoConfParse(ioscfg)
        res = parse.find_lines('ip route')
        return res

    def add_default_static_route(self, gw_ip, vrf):
        conn = self._get_connection()
        confstr = snippets.DEFAULT_ROUTE_CFG % (vrf, gw_ip)
        if not self._cfg_exists(confstr):
                confstr = snippets.SET_DEFAULT_ROUTE % (vrf, gw_ip)
                rpc_obj = conn.edit_config(target='running', config=confstr)
                print self._check_response(rpc_obj, 'SET_DEFAULT_ROUTE')

    def remove_default_static_route(self, gw_ip, vrf):
        conn = self._get_connection()
        confstr = snippets.DEFAULT_ROUTE_CFG % (vrf, gw_ip)
        if self._cfg_exists(confstr):
                confstr = snippets.REMOVE_DEFAULT_ROUTE % (vrf, gw_ip)
                rpc_obj = conn.edit_config(target='running', config=confstr)
                print self._check_response(rpc_obj, 'REMOVE_DEFAULT_ROUTE')

    def edit_running_config(self, confstr, snippet):
        conn = self._get_connection()
        rpc_obj = conn.edit_config(target='running', config=confstr)
        if self._check_response(rpc_obj, snippet):
                LOG.info("%s successfully executed" % snippet)
        else:
                LOG.exception("Failed executing %s" % snippet)
        return

    def _check_response(self, rpc_obj, snippet_name):
        LOG.debug("RPCReply for %s is %s" % (snippet_name, rpc_obj.xml))
        xml_str = rpc_obj.xml
        if "<ok />" in xml_str:
            return True
        else:
            """
            Response in case of error looks like this.
            We take the error type and tag.
            '<?xml version="1.0" encoding="UTF-8"?>
            <rpc-reply message-id="urn:uuid:81bf8082-....-b69a-000c29e1b85c"
            xmlns="urn:ietf:params:netconf:base:1.0">
                <rpc-error>
                    <error-type>protocol</error-type>
                    <error-tag>operation-failed</error-tag>
                    <error-severity>error</error-severity>
                </rpc-error>
            </rpc-reply>'
            """
            error_str = ("Error executing snippet %s "
                         "ErrorType:%s ErrorTag:%s ")
            logging.error(error_str, snippet_name, rpc_obj._root[0][0].text,
                          rpc_obj._root[0][1].text)
            raise Exception("Error!")
            return False

    def _check_response_non_working(self, rpc_obj, snippet_name):
        # ToDo(Hareesh): This is not working for some reason.
        # Kept for investigation
        LOG.debug("RPCReply for %s is %s" % (snippet_name, rpc_obj.xml))
        if rpc_obj.ok:
            return True
        else:
            raise rpc_obj.error
