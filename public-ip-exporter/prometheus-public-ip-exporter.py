#!/usr/bin/env python3
"""

Dependencies:
  apt install python3-prometheus-client
  apt install python3-requests
  apt install python3-uritools
  apt install python3-yaml

Config file format:

ip: 192.168.1.1
username: user
password: secret

endpoints:
  - rci/show/arp
  - rci/show/ip/nat

"""

import os
import sys

PROGRAMNAME = os.path.basename(sys.argv[0])
CONFIGFILE = os.path.splitext(PROGRAMNAME)[0] + ".conf"
LISTEN = ":8000"

import argparse
formatter = lambda prog: argparse.HelpFormatter(prog, max_help_position=27)
cli = argparse.ArgumentParser(
  prog = PROGRAMNAME,
  description = "Prometheus exporter for metrics of Keenetic home routers based on the Keenetic API.",
  epilog = "",
  formatter_class=formatter
)
cli.add_argument('-c', '--config-file', action='store', default=CONFIGFILE, help="location of the config file")
cli.add_argument('-l', '--listen', action='store', default=LISTEN, help="server address and port")
cli.add_argument('--self-metrics', action=argparse.BooleanOptionalAction, help="enable process metrics")
args = cli.parse_args()

import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
import prometheus_client as prom
from prometheus_client import Metric, CollectorRegistry, generate_latest, Gauge, Counter, REGISTRY
import requests
from string import Template

import syslog
syslog.openlog(logoption=syslog.LOG_PID)
def log(message): syslog.syslog(syslog.LOG_INFO,message)

import time
def now(): return int(time.time())

import traceback
from uritools import urisplit
import yaml
from yaml.loader import SafeLoader

class Config():

  def __init__(self, configfile):
    with open(configfile) as f:
      config = yaml.load(f, Loader=SafeLoader)
    self.__dict__ = config

class Collector():

  def collect(self):
    return metrics.values()

class Handler(BaseHTTPRequestHandler):

  config = Config(args.config_file)

  def do_GET(self):
    url = urisplit(self.path)
    params = url.getquerydict()

    self.error_message_format = """
      <!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN"
        "http://www.w3.org/TR/html4/strict.dtd">
      <html>
        <head>
          <meta http-equiv="Content-Type" content="text/html;charset=utf-8">
          <title>Error response</title>
        </head>
        <body>
          <h1>Error response</h1>
          <p>Error code: %(code)d</p>
          <p>Traceback: <pre>%(message)s</pre></p>
          <p>Error code explanation: %(code)s - %(explain)s.</p>
        </body>
      </html>
    """

    try:
      target = url.getquerydict().get('target')[0]
    except:
      trace = traceback.format_exc().strip()
      self.send_error(404, message=trace, explain="No target specified in query ...")
      return

    if (now() - data["timestamp"]) > 3600 or data.get("first_seen") is None: 
      url = Template(self.config.target[target]["url"]).substitute(token=self.config.target[target]["token"])
      log(f"Cached public IP address data expired - updating from {url}")
      r = requests.get(url)
      if r.status_code != 200:
        self.send_error(503, message=f"Backend API returned {r.status_code}", explain="Backend query failed.")
      data["ipinfo"] = r.json()

      if data["ipinfo"]["ip"] != r.json()["ip"]:
        # On newly assigned IP
        data["first_seen"] = now()

    if data.get("first_seen") is None:
      data["first_seen"] = now()
    data["age"] = now() - data["first_seen"]
    data["timestamp"] = now()

    metrics["public_ip"] = Metric("public_ip", f"Public IP provided by {target}", "untyped")
    metrics["public_ip"].add_sample("public_ip", value=data["age"], labels=data["ipinfo"])

    self.send_response(200)
    self.end_headers()
    self.wfile.write(generate_latest(registry))

if __name__ == '__main__':
  data = { "timestamp": now() }
  metrics = {}
  registry = CollectorRegistry()
  registry.register(Collector())
  if args.self_metrics:
    registry.register(prom.PROCESS_COLLECTOR)
    registry.register(prom.PLATFORM_COLLECTOR)
    registry.register(prom.GC_COLLECTOR)

  address, port = args.listen.split(":")
  log(f"Starting {PROGRAMNAME} on {address}:{port} ...")
  server = HTTPServer((address, int(port)), Handler)
  server.serve_forever()

