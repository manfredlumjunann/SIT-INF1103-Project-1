import json
from pathlib import Path

#Current Contains the following
#A. 
#B. 
#C. 
#D. 

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
print(data["total_flagged"]) #Displays Total number of flagged files

for clause in data["clauses"]: 
    print(clause["risk_level"], ": \n") #Risk assessment (High, med, low)

    #for references in clause["legal_references"]:
        #print(references["title"], "\n",references["url"], "\n") #currently display all links

    #Display all values that are in High
    if clause["risk_level"] == "HIGH":
        for reference in clause["legal_references"]:
            Clauses[clause["risk_level"]].append(reference["summary"])#Summary
            Clauses[clause["risk_level"]].append(reference["url"])#Citations
            Clauses[clause["risk_level"]].append(clause["workaround"])#Work around
            #print(reference["summary"])
            #print(reference["title"])
            #print(reference["url"], "\n")
        #print(clause["workaround"], "\n") #<-- Workarounds need to be outside
        #pass

    #Display all risk that are in Medium
    elif clause["risk_level"] == "MEDIUM":
        for reference in clause["legal_references"]:
            Clauses[clause["risk_level"]].append(reference["url"])


    #Display all risk that are in Low
    elif clause["risk_level"] == "LOW":
        for reference in clause["legal_references"]:
            Clauses[clause["risk_level"]].append(reference["url"])
        

    
print(Clauses)




