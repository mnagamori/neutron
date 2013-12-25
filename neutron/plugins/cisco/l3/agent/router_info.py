
N_ROUTER_PREFIX = 'nrouter-'


class RouterInfo(object):

    def __init__(self, router_id, router):
        self.router_id = router_id
        self.ex_gw_port = None
        self._snat_enabled = None
        self._snat_action = None
        self.internal_ports = []
        self.floating_ips = []
        self.router = router
        self.routes = []
        self.ha_info = None
        # Set 'ha_info' if present
        if router.get('ha_info') is not None:
            self.ha_info = router['ha_info']

    @property
    def router(self):
        return self._router

    @property
    def snat_enabled(self):
        return self._snat_enabled

    @router.setter
    def router(self, value):
        self._router = value
        if not self._router:
            return
        # enable_snat by default if it wasn't specified by plugin
        self._snat_enabled = self._router.get('enable_snat', True)

    def router_name(self):
        return N_ROUTER_PREFIX + self.router_id
