import json
from pathlib import Path
from pprint import pprint

import requests

def send_msg(text, data):
    # Your credentials
    token = "8976136444:AAFfIjAHnZwAL_MZoJLBd0wOCImfbmBCduE" #Token key
    don_id = "667740965"  # Make sure to replace this with your real numerical ID
    man_id = "1116849976" # Another user key
    inf_group_id = "-5212170665" # INF-CauseHandler
    # Base API URL
    url_req = f"https://api.telegram.org/bot{token}/sendMessage"
    
    # Safely passing parameters ensures spaces and special characters are handled correctly
    
    payload = {
        "chat_id": don_id, #Send to particular ChatID
        "text": text + "clauses: " + str(data)
    }
    
    # Make the request
    results = requests.get(url_req, params=payload)
    print(results.json())

# Test the function



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

if data["total_flagged"] > 5: #This one can send if threshold meets
     send_msg("Hello Manfred\nClauses Found: ", data["total_flagged"])
