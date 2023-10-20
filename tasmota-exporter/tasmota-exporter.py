#!/usr/bin/env python3

# Dependencies:
#  apt install python3-prometheus-client
#  apt install python3-requests
#  apt install python3-uritools
#  apt install python3-yaml

"""
Prometheus exporter for metrics of Tasmota WiFi Socket A1T using
the Tasmota API interface
"""

import os
import sys
import traceback
import logging
import logging.handlers
import json

import argparse
from argparse import BooleanOptionalAction
from http.server import HTTPServer, BaseHTTPRequestHandler
from icecream import ic
import prometheus_client as prom
from prometheus_client import Metric, CollectorRegistry, generate_latest
import requests
from uritools import urisplit
import yaml
from yaml.loader import SafeLoader

PROGRAMNAME = os.path.basename(sys.argv[0])
CONFIGFILE = os.path.splitext(PROGRAMNAME)[0] + '.conf'
LISTEN = ':8000'

def formatter(prog): return argparse.HelpFormatter(prog, max_help_position=45)
cli = argparse.ArgumentParser(
  prog = PROGRAMNAME,
  description = __doc__,
  epilog = "",
  formatter_class=formatter
)
cli.add_argument('-c', '--config', action='store', default=CONFIGFILE, help="config file location")
cli.add_argument('-l', '--listen', action='store', default=LISTEN, help="server address and port")
cli.add_argument('--self-metrics', action=BooleanOptionalAction, help="enable process metrics")
args = cli.parse_args()

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)
loghandler = logging.handlers.SysLogHandler(address = '/dev/log')
log.addHandler(loghandler)

if not args.self_metrics:
  prom.REGISTRY.unregister(prom.PROCESS_COLLECTOR)
  prom.REGISTRY.unregister(prom.PLATFORM_COLLECTOR)
  prom.REGISTRY.unregister(prom.GC_COLLECTOR)

class Tasmota():

  def __init__(self, config):
    self.config = config

  def request(self, ip, endpoint):
    url = 'http://' + ip + endpoint
    return requests.get(url, timeout=10)

  def register(self, metrics, self_metrics=False):
    """ Register a new Prometheus client registry """
    class Collector():
      """ Prometheus Client Collector class """
      def collect(self):
        """ Prometheus Client collect() function """
        return metrics.values()

    registry = CollectorRegistry()
    registry.register(Collector())

    if self_metrics:
      registry.register(prom.PROCESS_COLLECTOR)
      registry.register(prom.PLATFORM_COLLECTOR)
      registry.register(prom.GC_COLLECTOR)

    return registry

  def getSensorData(self, target, module):
    """
      Returning metrics for Tasmota Wifi Socket A1T
      API endpoint: /cm&cmnd=status+0
    """
    metrics = {}
    #labels = {}

    result = b""
    username = self.config.targets[target]["username"]
    password = self.config.targets[target]["password"]
    r = self.request(target, f"/cm?user={username}&password={password}&cmnd=status+0")
    r = json.loads(r.text)
    r = {k: v or 0 for (k, v) in r.items()}

    ic(r)
    r["__data"] = r[module]["ENERGY"]
    for (metric, metricMetaData) in self.config.modules[module]["metrics"].items():
      description = f"Tasmota WiFi socket A1T metric {metric}"
      metrictype = "untyped"
      unit = ""
      unitsymbol = ""
      if metricMetaData:
         if "description" in metricMetaData:
           description = metricMetaData["description"]
         if "metrictype" in metricMetaData:
           metrictype = metricMetaData["metrictype"]
         if "unit" in metricMetaData:
           unit = metricMetaData["unit"]
         if "unitsymbol" in metricMetaData:
           unitsymbol = metricMetaData["unitsymbol"]
      metrics[metric] = Metric(metric, description, metrictype, f"{unit}_{unitsymbol}")
      if not r["__data"][metric]: r["__data"][metric] = 0
      metrics[metric].add_sample(metric, value=r["__data"][metric], labels="")

    registry = self.register(metrics)
    result = generate_latest(registry)

    return result

class Config():

  def __init__(self, configfile):
    config = {}

    try:
      f = open(configfile, encoding="utf-8")
    except OSError:
      ic("Could not open/read file: " + configfile)
      traceback.format_exc().strip()
      sys.exit()

    with f:
      config = yaml.load(f, Loader=SafeLoader)
      self._config = config


  def __getattr__(self, key):
    try:
      return self._config[key]
    except KeyError as e:
      ic(e)
      raise AttributeError(key) from e

class Handler(BaseHTTPRequestHandler):
  """ HTTP server request handler class """

  config = Config(args.config)
  sensor = Tasmota(config)

  # pylint: disable=invalid-name; Method provided by upstream class
  def do_GET(self):
    """ Provide data in Prometheus Exposition Format upon client request """
    url = urisplit(self.path)

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

    # Avoid errors from browsers auto-requesting favicons
    if self.path == '/favicon.ico':
      self.send_response(200)
      self.send_header('Content-Type', 'image/x-icon')
      self.send_header('Content-Length', 0)
      self.end_headers()
      return

    query_params = url.getquerydict()
    if "target" in query_params:
      target = url.getquerydict().get('target')[0]
    else:
      self.send_error(404, message="No target!", explain="No target specified in query ...")
      return

    if target not in self.config.targets:
      self.send_error(404, message="Target does not exist!", explain=f"Target {target} not configured ...")
      return

    # Prometheus is quering multiple modules in one request -> multiple 'module' params possible
    modules = url.getquerydict().get('module')
    if modules is None:
      self.send_error(404, message="No module!", explain="No target specified in query ...")
      return

    metrics = b""
    for module in modules:
      metrics = b"".join([metrics, self.sensor.getSensorData(target, module)])

    self.send_response(200)
    self.end_headers()
    self.wfile.write(metrics)

if __name__ == '__main__':
  address, port = args.listen.split(":")
  print(f"Starting {PROGRAMNAME} on {address}:{port} ...")
  server = HTTPServer((address, int(port)), Handler)
  server.serve_forever()
