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

import datetime

from oslo.config import cfg
from neutron.openstack.common import log as logging
from neutron.agent.linux import utils as linux_utils
from neutron.openstack.common import timeutils
from neutron.openstack.common import importutils

from neutron.plugins.cisco.l3.agent.router_info import RouterInfo
from neutron.plugins.cisco.l3.common import constants as cl3_constants

LOG = logging.getLogger(__name__)

OPTS = [
    cfg.IntOpt('hosting_device_dead_timeout', default=300,
               help=_("The time in seconds until a backlogged "
                      "hosting device is presumed dead ")),
    cfg.StrOpt('CSR1kv_Routing_Driver', default='neutron.plugins.cisco.'
                                                'l3.agent.csr1000v.'
                                                'csr1000v_routing_driver.'
                                                'CSR1000vRoutingDriver',
               help=_("CSR1000v Routing Driver class")),
]
cfg.CONF.register_opts(OPTS)


class HostingDevicesManager(object):
    """
    This class manages different hosting devices eg: CSR1000v.

    It binds the different logical resources (eg: routers) and where they are
    hosted. It initialises and maintains drivers, that configuring these
    hosting devices for various services. Drivers are per hosting device and
    thus they are reused, if multiple resources (of the same kind) are
    configured by the same hosting device
    """

    def __init__(self):
        self.router_id_hosting_devices = {}
        self._drivers = {}
        self.backlog_hosting_devices = {}
        self.host_driver_binding = {
            cl3_constants.CSR_ROUTER_TYPE: cfg.CONF.CSR1kv_Routing_Driver
        }

    def get_driver(self, router_info):
        if isinstance(router_info, RouterInfo):
            router_id = router_info.router_id
        else:
            raise TypeError("Expected RouterInfo object. "
                            "Got %s instead"), type(router_info)
        hosting_device = self.router_id_hosting_devices.get(router_id, None)
        if hosting_device is not None:
            driver = self._drivers.get(hosting_device['id'], None)
            if driver is None:
                driver = self._set_driver(router_info)
        else:
            driver = self._set_driver(router_info)
        return driver

    def _set_driver(self, router_info):
        try:
            _driver = None
            router_id = router_info.router_id
            router = router_info.router

            hosting_device = router['hosting_device']
            _hd_id = hosting_device['id']
            _hd_type = hosting_device['host_type']
            _hd_ip = hosting_device['ip_address']
            _hd_port = hosting_device['port']

            #Retreiving auth info from RPC or use defaults if absent
            _hd_user = hosting_device.get('user', 'stack')
            _hd_passwd = hosting_device.get('password', 'cisco')
            # Lookup driver
            try:
                driver_class = self.host_driver_binding[_hd_type]
            except KeyError:
                LOG.exception((_("Cannot find driver class for "
                                 "device type %s "), _hd_type))
                raise
            #Load the driver
            try:
                _driver = importutils.import_object(
                    driver_class,
                    _hd_ip, _hd_port, _hd_user, _hd_passwd)
                # _driver = None
            except ImportError:
                LOG.exception(_("Error loading hosting device driver "
                                "%(driver)s for host type %(host_type)s"),
                              {'driver': driver_class,
                               'host_type': _hd_type})
                raise
            self.router_id_hosting_devices[router_id] = hosting_device
            self._drivers[_hd_id] = _driver
        except (AttributeError, KeyError) as e:
            LOG.error(_("Cannot set driver for router. Reason: %s"), e)
        return _driver

    def clear_driver_connection(self, he_id):
            driver = self._drivers.get(he_id, None)
            if driver:
                driver.clear_connection()
                LOG.debug(_("Cleared connection @ %s"), driver._csr_host)

    def remove_driver(self, router_id):
        del self.router_id_hosting_devices[router_id]
        for he_id in self._drivers.keys():
            if he_id not in self.router_id_hosting_devices.values():
                del self._drivers[he_id]

    def pop(self, he_id):
        self._drivers.pop(he_id, None)

    def get_backlogged_hosting_entities(self):
        return {he_id: {'affected routers': data['routers']} for he_id, data
                in self.backlog_hosting_devices.items()}

    def is_hosting_entity_reachable(self, router_id, router):
        hd = router['hosting_device']
        he_id = hd['id']
        he_mgmt_ip = hd['ip_address']
        #Modifying the 'created_at' to a date time object
        hd['created_at'] = datetime.datetime.strptime(hd['created_at'],
                                                      '%Y-%m-%d %H:%M:%S')

        if not he_id in self.backlog_hosting_devices.keys():
            if self._is_pingable(he_mgmt_ip):
                LOG.debug(_("Hosting device: %(he_id)s @ %(ip)s for router: "
                            "%(id)s is reachable."),
                          {'he_id': he_id, 'ip': hd['ip_address'],
                           'id': router_id})
                return True
            else:
                LOG.debug(_("Hosting device: %(he_id)s @ %(ip)s for router: "
                            "%(id)s is NOT reachable."),
                          {'he_id': he_id, 'ip': hd['ip_address'],
                           'id': router_id, })
                hd['backlog_insertion_ts'] = max(
                    timeutils.utcnow(),
                    hd['created_at'] +
                    datetime.timedelta(seconds=hd['booting_time']))
                self.backlog_hosting_devices[he_id] = {'hd': hd,
                                                       'routers': [router_id]}
                self.clear_driver_connection(he_id)
                LOG.debug(_("Hosting device: %(he_id)s @ %(ip)s is now added "
                            "to backlog"), {'he_id': he_id,
                                            'ip': hd['ip_address']})
        else:
            self.backlog_hosting_devices[he_id]['routers'].append(router_id)
        return False

    def check_backlogged_hosting_entities(self):
        """" Checks the status of backlogged hosting entities.
        Has the intelligence to give allowance for the booting time for
        newly spun up instances. Sends back a response dict of the format:
        {'reachable': [<he_id>,..], 'dead': [<he_id>,..]}  """
        response_dict = {'reachable': [],
                         'dead': []}
        for he_id in self.backlog_hosting_devices.keys():
            he = self.backlog_hosting_devices[he_id]['he']
            if not timeutils.is_older_than(he['created_at'],
                                           he['booting_time']):
                LOG.info(_("Hosting entity: %(he_id)s @ %(ip)s hasn't passed "
                           "minimum boot time. Skipping it. "),
                         {'he_id': he_id, 'ip': he['ip_address']})
                continue
            LOG.info(_("Checking hosting entity: %(he_id)s @ %(ip)s for "
                       "reachability."), {'he_id': he_id,
                                          'ip': he['ip_address']})
            if self._is_pingable(he['ip_address']):
                he.pop('backlog_insertion_ts', None)
                del self.backlog_hosting_devices[he_id]
                response_dict['reachable'].append(he_id)
                LOG.info(_("Hosting entity: %(he_id)s @ %(ip)s is now "
                           "reachable. Adding it to response"),
                         {'he_id': he_id, 'ip': he['ip_address']})
            else:
                LOG.info(_("Hosting entity: %(he_id)s @ %(ip)s still not "
                           "reachable "), {'he_id': he_id,
                                           'ip': he['ip_address']})
                if timeutils.is_older_than(
                        he['backlog_insertion_ts'],
                        int(cfg.CONF.hosting_device_dead_timeout)):
                    LOG.debug(_("Hosting entity: %(he_id)s @ %(ip)s hasn't "
                                "been reachable for the last %(time)d "
                                "seconds. Marking it dead."),
                              {'he_id': he_id, 'ip': he['ip_address'],
                               'time': cfg.CONF.hosting_device_dead_timeout})
                    response_dict['dead'].append(he_id)
                    he.pop('backlog_insertion_ts', None)
                    del self.backlog_hosting_devices[he_id]
        LOG.debug(_("Response: %s"), response_dict)
        return response_dict

    def _is_pingable(self, mgmt_ip):
        r = self._send_ping(mgmt_ip)
        if r:
            return False
        else:
            return True

    def _send_ping(self, ip):
        ping_cmd = ['ping',
                    '-c', '5',
                    '-W', '1',
                    '-i', '0.2',
                    ip]
        try:
            linux_utils.execute(ping_cmd, check_exit_code=True)
        except RuntimeError:
            LOG.warn(_("Cannot ping ip address: %s"), ip)
            return -1
