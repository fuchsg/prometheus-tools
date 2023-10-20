#!/usr/bin/env python3

import json

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


options = Options()
options.add_argument("--headless=new")
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
driver.set_window_position(2000, 50)

driver.get("https://www.netztest.at/de/Test")
wait = WebDriverWait(driver, 120)
element = wait.until(EC.presence_of_element_located((By.ID, 'modal-confirm')))
element.click()
wait.until(EC.presence_of_element_located((By.ID, 'verlauf-detail')))
WebDriverWait(driver, 10).until(EC.text_to_be_present_in_element((By.TAG_NAME, "td"), "Download"))

#print(driver.get_cookies())
#cookies = driver.get_cookies()
#driver.get("https://c01.netztest.at/RMBTControlServer/testresultdetail")
#for cookie in cookies:
#  driver.add_cookie(cookie)
#print(driver.page_source)

data = {}

table = driver.find_element(By.ID, "verlauf-detail")
rows = table.find_elements(By.TAG_NAME, "tr")
for row in rows:
   key, value = row.find_elements(By.TAG_NAME, "td")
   data[key.text] = value.text

table = driver.find_element(By.ID, "testresult-detail")
rows = table.find_elements(By.TAG_NAME, "tr")
for row in rows:
   key, value = row.find_elements(By.TAG_NAME, "td")
   data[key.text] = value.text

json = json.dumps(data)
print(json)

driver.quit()
