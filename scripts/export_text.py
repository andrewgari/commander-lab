import sys
import requests

def get_archidekt_deck(deck_id):
    url = f"https://archidekt.com/api/decks/{deck_id}/"
    res = requests.get(url)
    res.raise_for_status()
    return res.json()

def format_deck_for_export(deck_data):
    # CommanderTemplate and Moxfield usually accept MTGO format: "1 Card Name"
    # We will separate commanders from the mainboard just in case.
    
    commanders = []
    mainboard = []
    
    for card in deck_data.get("cards", []):
        qty = card.get("quantity", 1)
        name = card.get("card", {}).get("oracleCard", {}).get("name", "Unknown")
        categories = card.get("categories", [])
        
        line = f"{qty} {name}"
        
        if "Commander" in categories:
            commanders.append(line)
        else:
            mainboard.append(line)
            
    output = []
    if commanders:
        output.append("// Commander")
        output.extend(commanders)
        output.append("")
        
    output.append("// Mainboard")
    output.extend(mainboard)
    
    return "\n".join(output)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python export_text.py <archidekt_deck_id>")
        sys.exit(1)
        
    deck_id = sys.argv[1]
    deck = get_archidekt_deck(deck_id)
    text_list = format_deck_for_export(deck)
    print(text_list)
