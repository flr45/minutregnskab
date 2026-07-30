from pathlib import Path

root = Path(__file__).resolve().parents[1]
html_path = root / "templates" / "tidsregistrering.html"
test_path = root / "tests" / "calculations.test.mjs"
worker_path = root / "static" / "service-worker.js"

html = html_path.read_text()
replacements = [
    (
        'import {calculateTrip,calculateSummary,tripsOverlap} from "{{ url_for(\'static\', filename=\'js/calculations.js\') }}";',
        'import {calculateTrip,calculateSummary,evaluatePauseStatus,tripsOverlap} from "{{ url_for(\'static\', filename=\'js/calculations.js\') }}";',
    ),
    (
        'function evaluatePause(start,end){const now=elapsedMinutes();if(now<start)return null;const segments=state.trips.map(t=>{const r=calculateTrip(t.start,t.end,state.start);return [Math.max(start,r.start),Math.min(end,r.end)]}).filter(([s,e])=>e>s).sort((a,b)=>a[0]-b[0]);let cursor=start,interrupted=false,hadGap=false;for(const [s,e] of segments){if(s>cursor){const gap=s-cursor;hadGap=true;if(gap>=30)return "";interrupted=true}cursor=Math.max(cursor,e)}const visibleEnd=Math.min(end,now);if(visibleEnd>cursor){const gap=visibleEnd-cursor;hadGap=true;if(gap>=30)return ""}if(now<end)return interrupted?"Afbrudt":null;if(cursor<end&&end-cursor>=30)return "";if(interrupted)return "Afbrudt";return hadGap?"Ikke afholdt":"Ikke afholdt"}',
        'function evaluatePause(start,end){return evaluatePauseStatus(state.trips,state.start,start,end,elapsedMinutes())}',
    ),
    (
        'function updateAutomaticPauses(){if(!state.date||!state.start)return;for(const [name,start,end] of [["pause1",180,360],["pause2",600,780]]){if(state[`${name}Manual`])continue;const result=evaluatePause(start,end);if(result!==null)state[name]=result}for(const [name,start,end] of [["pause1",180,360],["pause2",600,780]]){$(`${name}Note`).textContent=`Interval ${pauseClock(start)}–${pauseClock(end)} · seneste start ${pauseClock(end-30)}${state[`${name}Manual`]?" · manuelt valg":" · automatisk"}`;document.querySelectorAll(`[name=${name}]`).forEach(r=>r.checked=r.value===state[name])}}',
        'function updateAutomaticPauses(){if(!state.date||!state.start)return;for(const [name,start,end] of [["pause1",180,360],["pause2",600,780]]){const manualKey=`${name}Manual`;if(state[manualKey]&&!state[name])state[manualKey]=false;if(state[manualKey]||state[name]==="Afbrudt")continue;const result=evaluatePause(start,end);if(result!==null)state[name]=result}for(const [name,start,end] of [["pause1",180,360],["pause2",600,780]]){$(`${name}Note`).textContent=`Interval ${pauseClock(start)}–${pauseClock(end)} · seneste start ${pauseClock(end-30)}${state[`${name}Manual`]?" · manuelt valg":" · automatisk"}`;document.querySelectorAll(`[name=${name}]`).forEach(r=>r.checked=r.value===state[name])}}',
    ),
    (
        'document.querySelectorAll(\'input[type=radio]\').forEach(r=>r.addEventListener("click",e=>{const name=r.name,wasSelected=state[name]===r.value;if(wasSelected){e.preventDefault();if(!confirm(`Vil du fjerne valget “${r.value}” for ${name==="pause1"?"pause 1":"pause 2"}?`)){r.checked=true;return}state[name]="";state[`${name}Manual`]=true;r.checked=false}else{state[name]=r.value;state[`${name}Manual`]=true}render();scheduleSave()}));',
        'document.querySelectorAll(\'input[type=radio]\').forEach(r=>r.addEventListener("click",e=>{const name=r.name,wasSelected=state[name]===r.value;if(wasSelected){e.preventDefault();if(!confirm(`Vil du fjerne valget “${r.value}” for ${name==="pause1"?"pause 1":"pause 2"} og slå automatikken til igen?`)){r.checked=true;return}state[name]="";state[`${name}Manual`]=false;r.checked=false}else{state[name]=r.value;state[`${name}Manual`]=true}render();scheduleSave()}));',
    ),
]

for old, new in replacements:
    if old not in html:
        raise SystemExit(f"Forventet HTML-kode blev ikke fundet: {old[:80]}")
    html = html.replace(old, new, 1)
html_path.write_text(html)

tests = test_path.read_text()
tests = tests.replace(
    'import { calculateSummary, calculateTrip, tripsOverlap } from "../static/js/calculations.js";',
    'import { calculateSummary, calculateTrip, evaluatePauseStatus, tripsOverlap } from "../static/js/calculations.js";',
    1,
)
if 'en tur inden for pausens første 30 minutter' not in tests:
    tests += '''\n\ntest("en tur inden for pausens første 30 minutter registrerer afbrudt pause", () => {\n  const result = evaluatePauseStatus(\n    [{ start: "10:40", end: "11:08" }],\n    "07:30",\n    180,\n    360,\n    291,\n  );\n  assert.equal(result, "Afbrudt");\n});\n\ntest("30 sammenhængende minutter før første tur betyder afholdt pause", () => {\n  const result = evaluatePauseStatus(\n    [{ start: "11:05", end: "11:30" }],\n    "07:30",\n    180,\n    360,\n    250,\n  );\n  assert.equal(result, "");\n});\n\ntest("ingen 30 minutters pause ved intervallets slutning betyder ikke afholdt", () => {\n  const result = evaluatePauseStatus(\n    [{ start: "10:30", end: "13:20" }],\n    "07:30",\n    180,\n    360,\n    360,\n  );\n  assert.equal(result, "Ikke afholdt");\n});\n\ntest("en igangværende kort pause markeres ikke før den afbrydes", () => {\n  const result = evaluatePauseStatus([], "07:30", 180, 360, 190);\n  assert.equal(result, null);\n});\n'''
test_path.write_text(tests)

worker = worker_path.read_text()
if 'minutregnskab-v2-7' not in worker:
    raise SystemExit("Forventet PWA-cacheversion blev ikke fundet")
worker_path.write_text(worker.replace('minutregnskab-v2-7', 'minutregnskab-v2-8', 1))

(root / ".github" / "workflows" / "apply-pause-hotfix.yml").unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)
