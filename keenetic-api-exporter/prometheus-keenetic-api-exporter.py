#!/usr/bin/env python3

# Dependencies:
#  apt install python3-prometheus-client
#  apt install python3-requests
#  apt install python3-uritools
#  apt install python3-yaml

# Config file format:

# ip: 192.168.1.1
# username: user
# password: secret

# endpoints:
#  - rci/show/arp
#  - rci/show/ip/nat

"""
Prometheus exporter for metrics of Keenetic home routers based on the Keenetic API.
"""

import os
import sys
import traceback

import argparse
from argparse import BooleanOptionalAction
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
import logging
import logging.handlers
import prometheus_client as prom
from prometheus_client import Metric, CollectorRegistry, generate_latest
import requests
from uritools import urisplit
import yaml
from yaml.loader import SafeLoader

PROGRAMNAME = os.path.basename(sys.argv[0])
CONFIGFILE = os.path.splitext(PROGRAMNAME)[0] + '.conf'
LISTEN = ':8000'

formatter = lambda prog: argparse.HelpFormatter(prog, max_help_position=45)
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

class Keenetic():
  """ Keenetic API client class """

  def __init__(self, configfile):
    with open(configfile) as f:
      self.config = yaml.load(f, Loader=SafeLoader)

    self.session = requests.session()

  def auth(self, target):
    """ Keenetic session authentication for later use in API requests """
    ip = target
    username = self.config['auth'][target]['username']
    password = self.config['auth'][target]['password']

    try:
      response = self.request(ip, 'auth')
    except requests.exceptions.RequestException as e:
      log.error(e)
      return False

    if response.status_code == 401:
      md5 = username + ':' + response.headers['X-NDM-Realm'] + ':' + password
      md5 = hashlib.md5(md5.encode('utf-8'))
      sha = response.headers['X-NDM-Challenge'] + md5.hexdigest()
      sha = hashlib.sha256(sha.encode('utf-8'))
      response = self.request(ip, "auth", {'login': username, 'password': sha.hexdigest()})
      if response.status_code == 200:
        return True
    elif response.status_code == 200:
      return True

    return False

  def request(self, ip, query, post = None):
    """ Sending a Keenetic API request to endpoint in 'query' """
    url = 'http://' + ip + '/' + query
    if post:
      return self.session.post(url, json=post)

    return self.session.get(url)

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

  def system(self, target):
    """
      Returning general system metrics
      API endpoint: rci/show/system
    """
    result = b""
    if self.auth(target):
      r = self.request(target, "rci/show/system")
      r = r.json()

      labels = {}
      for label in self.config["modules"]["system"]["labels"]:
        labels[label] = r[label]

      metrics = {}
      for metric in self.config["modules"]["system"]["metrics"]:
        metrics[metric] = Metric(metric, f"Keenetic system metric {metric}", "untyped")
        metrics[metric].add_sample(metric, value=r[metric], labels=labels)

      registry = self.register(metrics)
      result = generate_latest(registry)

    return result

  def interface(self, target):
    """
      Returning interface metrics for all interfaces found in 'state' == 'up'
      API endpoint: rci/show/interface/<interface-name>
    """
    result = b""
    if self.auth(target):
      interfaces = self.request(target, "rci/show/interface")
      interfaces = interfaces.json()

      # Get all interfaces with link "up"
      for i in interfaces:
        if interfaces[i]["link"] == "up":
          name = interfaces[i]['interface-name']
          stats = self.request(target, f"rci/show/interface/stat?name={name}")
          stats = stats.json()

          labels = {}
          for label in self.config["modules"]["interface"]["labels"]:
          # Skipping labels not avaiable for an interface
            try:
              l = label.replace("-", "_")
              labels[l] = interfaces[i][label]
            except KeyError:
              continue

          # Exposing all metrics found in interface statistics (no configuration)
          metrics = {}
          for metric in stats:
            m = metric.replace("-", "_")
            metrics[m] = Metric(m, f"Keenetic interface metric {metric}", "untyped")
            metrics[m].add_sample(m, value=stats[metric], labels=labels)

          registry = self.register(metrics)
          result = b"".join([result, generate_latest(registry)])

    return result

class Handler(BaseHTTPRequestHandler):
  """ HTTP server request handler class """

  keenetic = Keenetic(args.config)

  # pylint: disable=C0103; Method provided by upstream class
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

    target = url.getquerydict().get('target')[0]
    if target is None:
      self.send_error(404, message="No target!", explain="No target specified in query ...")
      return
    # Prometheus is quering multiple modules in one request -> multiple 'module' params possible
    modules = url.getquerydict().get('module')
    if modules is None:
      self.send_error(404, message="No module!", explain="No target specified in query ...")
      return

    metrics = b""
    for module in modules:
      if module == "system":
        metrics = b"".join([metrics, self.keenetic.system(target)])
      elif module == "interface":
        metrics = b"".join([metrics, self.keenetic.interface(target)])
      else:
        trace = traceback.format_exc().strip()
        self.send_error(404, message=trace, explain=f"Cannot find module {module}.")

    self.send_response(200)
    self.end_headers()
    self.wfile.write(metrics)

if __name__ == '__main__':
  address, port = args.listen.split(":")
  print(f"Starting {PROGRAMNAME} on {address}:{port} ...")
  server = HTTPServer((address, int(port)), Handler)
  server.serve_forever()
