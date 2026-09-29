import json
from pathlib import Path
from pprint import pprint
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



