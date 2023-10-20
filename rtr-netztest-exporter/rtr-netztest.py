#!/usr/bin/env python3

import os
import sys
import syslog

from datetime import datetime
import math
import pprint
from pyvirtualdisplay import Display
import requests

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# module pprint
pp = pprint.PrettyPrinter(indent=2)

# module pyvirtualdisplay
display = Display(visible=0, size=(1300, 2675))
display.start()

# module selenium
options = Options()
# Enable options below to run as root
#options.add_argument("--user-data-dir")
#options.add_argument("--test-type")
#options.add_argument("--no-sandbox")
options.add_argument("--hide-scrollbars")
options.BinaryLocation = "/usr/bin/chromium-browser"
driver = webdriver.Chrome(options=options, service=Service("/usr/bin/chromedriver"))

# module syslog
syslog.openlog(logoption=syslog.LOG_PID)
log = syslog.syslog
log("Starting test run ...")

DATE = str(datetime.now().date())
EVIDENCE_DIR = "/var/www/html/evidence/" + DATE
INF = math.inf

# pylint: disable=consider-using-namedtuple-or-dataclass
# Will be moved to a config file
t = {
  'download': {
    'bad': { 'lower': 0, 'upper': 9 },
    'average': { 'lower': 10, 'upper': 19 },
    'good': { 'lower': 20, 'upper': INF }
  },
  'upload': {
    'bad': { 'lower': 0, 'upper': 4 },
    'average': { 'lower': 5, 'upper': 9 },
    'good': { 'lower': 10, 'upper': INF }
  },
  'ping': {
    'bad': { 'lower': 50, 'upper': INF },
    'average': { 'lower': 20, 'upper': 49 },
    'good': { 'lower': 0, 'upper': 19 }
  }
}

numeric_fields = ['Download', 'Upload', 'Ping']

try:
  driver.get("https://www.netztest.at/de/Test")
  wait = WebDriverWait(driver, 120)
  element = wait.until(EC.presence_of_element_located((By.ID, 'modal-confirm')))
  element.click()
  wait.until(EC.presence_of_element_located((By.ID, 'testresult-detail')))
  WebDriverWait(driver, 10).until(EC.text_to_be_present_in_element((By.TAG_NAME, "td"), "Download"))
except Exception as e: # pylint: disable=broad-exception-caught
  # Catching all errors and terminate
  log("Failed to run RTR-Netztest: " + str(e))
  driver.quit()
  sys.exit()

if not os.path.exists(EVIDENCE_DIR):
  os.makedirs(EVIDENCE_DIR)
driver.save_screenshot(EVIDENCE_DIR + "/" + datetime.now().isoformat() + ".png")

table = driver.find_element(By.ID, "testresult-detail")
cells = table.find_elements(By.TAG_NAME, "td")
results = {}
for td in cells:
  if td.get_attribute('data-label'):
    key = td.get_attribute('data-label')
    if key in numeric_fields:
      value = float(td.text.split()[0].replace(",","."))
    else:
      value = td.text
    results[key] = value

for key in numeric_fields:
  # Assembling PEF (Prometheus Exposition Format)
  metric = key.lower()
  value = results[key]

  labels = 'job="rtr-netztest"'
  labels += f',date="{datetime.now().date()}"'
  labels += f',ip="{results["Externe IP"]}"'
  labels += f',provider="{results["Betreiber"]}"'
  if sys.stdout.isatty(): labels += ',mode="manual"'

  pef = f'{metric}{{{labels}}} {value} {round(datetime.now().timestamp()*1000)}'
  r = requests.post("http://localhost:8428/api/v1/import/prometheus", data=pef, timeout=5)
  log("Sending Prometheus Exposition Format data to Victoriametrics: " + pef)
  log("Victoriametrics API response: " + str(r.status_code))

  # Quality timeseries

  # Assign quality label based on thresholds
  for k in t[metric]:
    l = t[metric][k]
    if l['lower'] <= value <= l['upper']:
      quality = k
      break

  timestamp = round(datetime.now().timestamp()*1000)
  # For each quality level a seperate timeseries is created
  # to allow to show all quality levels in e.g. pie charts
  # even if the quality level does not exisit in the observed range.
  for k in t[metric]:
    value = 1 if k == quality else 0

    labels = 'job="rtr-netztest"'
    labels += f',metric="{metric}"'
    labels += f',quality="{k}"'
    labels += f',date="{datetime.now().date()}"'
    labels += f',ip="{results["Externe IP"]}"'
    labels += f',provider="{results["Betreiber"]}"'
    if sys.stdout.isatty(): labels += ',mode="manual"'

    pef = f'quality{{{labels}}} {value} {timestamp}'
    r = requests.post("http://localhost:8428/api/v1/import/prometheus", data=pef, timeout=5)
    log("Sending Prometheus Exposition Format data to Victoriametrics: " + pef)
    log("Victoriametrics API response: " + str(r.status_code))

driver.quit()
display.stop()
syslog.syslog("Finished test run ...")
