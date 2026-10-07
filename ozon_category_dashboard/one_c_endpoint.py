"""Approved 1C destinations routed through server-managed private SSH tunnels."""

def sql_endpoint(host, port):
    host, port = str(host).strip(), str(port).strip()
    routes = {
        ('172.19.0.1', '11433'): ('172.19.0.1', 11433),
        ('192.168.50.242', '1433'): ('172.19.0.1', 11434),
    }
    if (host, port) not in routes:
        raise ValueError('Unsupported 1C SQL endpoint')
    return routes[(host, port)]
