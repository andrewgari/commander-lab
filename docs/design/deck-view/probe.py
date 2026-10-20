import importlib.util
import pathlib
import subprocess
for name in ['playwright','PIL','fastapi','uvicorn','redis']:
    print(name, bool(importlib.util.find_spec(name)))
root=pathlib.Path(__file__).resolve().parents[3]
for name in ['partials/deck_head.html','deck_stats.html','deck_identity.html','deck_journal.html','deck_tune.html','deck_ideas.html','deck_review.html','deck_sim.html','deck_manage.html','deck_composition.html','deck_theory.html','deck_primer_build.html']:
    text=subprocess.check_output(['git','show','origin/main:templates/'+name],cwd=root,text=True)
    print('\n###',name)
    for line in text.splitlines():
        if any(x in line for x in ['<h1','<h2','<h3','<h4','hud.panel','dh.header','href=','block content']):
            print(line[:500])
