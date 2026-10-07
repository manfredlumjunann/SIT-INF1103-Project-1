import json
import os
from pathlib import Path
from pprint import pprint

import requests
from dotenv import load_dotenv

load_dotenv()

def send_msg(text, data):
    newdata = int(data)
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    don_id = "667740965"
    man_id = "1116849976"
    inf_group_id = "-5212170665"
    url_req = f"https://api.telegram.org/bot{token}/sendMessage"

    if newdata > 8:
        payload = {
            "chat_id": man_id,
            "text": text + "clauses: " + str(data)
        }
    elif newdata > 4:
        payload = {
            "chat_id": don_id,
            "text": text + "clauses: " + str(data)
        }
    else:
        return

    results = requests.get(url_req, params=payload)
    print(results.json())
filename = "Contasis.json" 
json_path = Path(__file__).with_name(filename) #<-- Modifiable file input
with json_path.open(encoding="utf-8") as file:
    data = json.load(file)

Clauses = {
    "HIGH": [],
    "MEDIUM": [],
    "LOW": []
}

def clause_levels(level):
    Clauses[level].append({
        "clausetype: ": clause["clause_type"],
        "workaround: ": clause["workaround"],
        "references: ": [
            {
                "summary": reference["summary"],
                #"title": reference["title"],
                "url": reference["url"]
            }
            for reference in clause["legal_references"]]
        })
    return

#Display all
print("Total Flag: ",data["total_flagged"], "\n") #Displays Total number of flagged files
for clause in data["clauses"]: 
    #print(clause["risk_level"], ": \n") #Risk assessment (High, med, low)
    r_lvl = clause["risk_level"]
    if r_lvl == "HIGH":
        clause_levels(r_lvl)
    elif r_lvl == "MEDIUM":
            clause_levels(r_lvl)
    elif r_lvl == "LOW":
            clause_levels(r_lvl)
    
print(Clauses)

if data["total_flagged"] > 5:
    send_msg("Clauses Found in recent scanned clause: ", data["total_flagged"])
