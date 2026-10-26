"""Unified card sourcing logic."""
from typing import Optional, Dict, Any, List, Mapping

def determine_source(
    card_name: str,
    is_owned: bool,
    available_copies: int,
    price: Optional[float],
    budget_cap: Optional[float],
    this_deck_score: Optional[float] = None,
    donor_decks: Optional[List[Dict[str, Any]]] = None,
    very_strong_threshold: float = 0.85
) -> Optional[Dict[str, Any]]:
    """
    Determine where a card should come from based on unified sourcing priority.
    
    1. Free inventory: Owned and not used elsewhere (available > 0).
    2. Buy: Not owned/free, but under budget.
    3. Donor deck: Not owned/free, VERY strong in this deck, and performs similarly or worse in donor.
    
    Returns a dict with 'source' and relevant metadata, or None if the card cannot be sourced.
    """
    # 1. Free inventory
    if is_owned and available_copies > 0:
        return {"source": "free"}
        
    # 2. Buy if under budget
    if price is not None:
        if budget_cap is None or price <= budget_cap:
            return {"source": "buy", "price": price}

    # 3. Take from another deck
    if this_deck_score is not None and donor_decks:
        if this_deck_score >= very_strong_threshold:
            best_donor = None
            best_delta = -float('inf')
            
            for donor in donor_decks:
                d_score = donor.get("donor_score")
                if d_score is not None and d_score <= this_deck_score:
                    delta = this_deck_score - d_score
                    if delta > best_delta:
                        best_delta = delta
                        best_donor = donor
            
            if best_donor:
                return {
                    "source": "donor",
                    "donor_deck": best_donor["name"],
                    "donor_score": best_donor["donor_score"]
                }
                
    return None
