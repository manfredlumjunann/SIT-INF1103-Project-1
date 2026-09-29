import json
from pathlib import Path

#takes output from JSON file, input into a dictionary

#Main Dict Keys: clauses, timestamp, total_flagged 
#Sub Dict keys: clause_text, clause_type, issue_description, legal_ferences, line_number, risk_level, workaround
#tertiary: summary, title, url

Clauses = {
    "HIGH": [],
    "MEDIUM": [],
    "LOW": []
}

filename = "Contasis.json" 
json_path = Path(__file__).with_name(filename) #<-- Modifiable file input
with json_path.open(encoding="utf-8") as file:
    data = json.load(file)


#Display all
print("Total Flag: ",data["total_flagged"]) #Displays Total number of flagged files

for clause in data["clauses"]: 
    print(clause["risk_level"], ": \n") #Risk assessment (High, med, low)
    if clause["risk_level"] == "HIGH":
        Clauses[clause["risk_level"]].append({
            "clausetype: ": clause["clause_type"]
            })
   


        
    
print(Clauses)

