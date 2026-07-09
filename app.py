import os, json, subprocess, sys, sqlite3, base64, io
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None
from datetime import datetime
from flask import Flask, request, jsonify, render_template, session, redirect, url_for
import anthropic

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "fibox-secret-key-change-in-prod")

# ── Per-user passwords ────────────────────────────────────────────────────────
# USERS env var accepts two formats:
#   Simple: user1:pass1,user2:pass2
#   JSON:   {"user1":"pass1","user2":"pass2"}
# Individual users can also be added via USER_<name>=<password> env vars.
_users_env = os.environ.get("USERS")
if _users_env:
    _users_env = _users_env.strip()
    if _users_env.startswith("{"):
        USERS = json.loads(_users_env)
    else:
        USERS = dict(pair.split(":", 1) for pair in _users_env.split(",") if ":" in pair)
else:
    USERS = {"default": os.environ.get("APP_PASSWORD", "Fibox_agent")}

# Merge any USER_<name>=<password> individual variables
for _k, _v in os.environ.items():
    if _k.startswith("USER_") and _v:
        USERS[_k[5:].lower()] = _v

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Fibox_admin")

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ── Database: PostgreSQL on Railway, SQLite locally ───────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, "usage.db")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
READ_DOC_PY = os.path.join(SCRIPTS_DIR, "read_doc.py")

DATABASE_URL = os.environ.get("DATABASE_URL")  # set automatically by Railway PostgreSQL
# psycopg2 requires postgresql:// not postgres://
if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
USE_PG = bool(DATABASE_URL and psycopg2)

def _pg():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    if USE_PG:
        import time
        for attempt in range(10):
            try:
                con = _pg()
                cur = con.cursor()
                cur.execute("""CREATE TABLE IF NOT EXISTS prompts (
                    id      SERIAL PRIMARY KEY,
                    ts      TEXT NOT NULL,
                    usr     TEXT NOT NULL,
                    ip      TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status  TEXT NOT NULL DEFAULT 'pending'
                )""")
                con.commit(); con.close()
                print(f"[DB] PostgreSQL ready (attempt {attempt+1})", flush=True)
                return
            except Exception as e:
                print(f"[DB] init_db attempt {attempt+1} failed: {e}", flush=True)
                time.sleep(2)
        print("[DB] ERROR: could not initialise PostgreSQL after 10 attempts", flush=True)
    else:
        con = sqlite3.connect(DB_PATH)
        con.execute("""CREATE TABLE IF NOT EXISTS prompts (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT NOT NULL,
            usr     TEXT NOT NULL,
            ip      TEXT NOT NULL,
            message TEXT NOT NULL,
            status  TEXT NOT NULL DEFAULT 'pending'
        )""")
        try:
            con.execute("ALTER TABLE prompts RENAME COLUMN user TO usr")
        except Exception:
            pass
        con.commit(); con.close()

init_db()
print(f"[DB] USE_PG={USE_PG}  DATABASE_URL={'set' if DATABASE_URL else 'NOT SET'}", flush=True)

def log_prompt(user, ip, message):
    """Insert a new row and return its id so status can be updated later."""
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    if USE_PG:
        con = _pg(); cur = con.cursor()
        cur.execute("INSERT INTO prompts (ts, usr, ip, message, status) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                    (ts, user, ip, message[:300], "pending"))
        row_id = cur.fetchone()[0]
        con.commit(); con.close()
    else:
        con = sqlite3.connect(DB_PATH)
        cur = con.execute("INSERT INTO prompts (ts, usr, ip, message, status) VALUES (?,?,?,?,?)",
                          (ts, user, ip, message[:300], "pending"))
        row_id = cur.lastrowid
        con.commit(); con.close()
    return row_id

def update_status(row_id, status):
    if USE_PG:
        con = _pg()
        con.cursor().execute("UPDATE prompts SET status=%s WHERE id=%s", (status, row_id))
        con.commit(); con.close()
    else:
        con = sqlite3.connect(DB_PATH)
        con.execute("UPDATE prompts SET status=? WHERE id=?", (status, row_id))
        con.commit(); con.close()


def logged_in():
    return session.get("user") is not None

SEARCH_PY   = os.path.join(SCRIPTS_DIR, "search_enclosures.py")
SCRAPE_PY   = os.path.join(SCRIPTS_DIR, "scrape_fibox.py")
LIST_PY     = os.path.join(SCRIPTS_DIR, "list_by_group.py")
LOOKUP_PY   = os.path.join(SCRIPTS_DIR, "lookup_by_code.py")
SYMBOL_PY   = os.path.join(SCRIPTS_DIR, "lookup_by_symbol.py")
CONTACTS_PY = os.path.join(SCRIPTS_DIR, "contacts_lookup.py")

SYSTEM_PROMPT = """You are a Fibox product specialist assistant. Help customers find the right Fibox enclosure.

## About Fibox
Fibox is a Finnish manufacturer of polycarbonate (PC), ABS, and GRP enclosures used in industrial, electrical, and outdoor applications.

## Product Families
- ARCA: Classic wall-mount cabinets, PC or steel door
- MNX: Compact polycarbonate enclosures
- EURONORD: DIN-rail and wall-mount enclosures, PC/ABS/Polyester
- TEMPO: Lightweight ABS/PC enclosures
- SOLID: Heavy-duty GRP enclosures
- CAB: ABS and PC enclosures
- ACCE: Accessories
- CABLE GLANDS: Cable entries and glands

## Dimension Convention
All dimensions are in millimetres: Width x Depth x Height.
Convert cm to mm if the customer uses centimetres (multiply by 10).

## Tool Selection Rules
- Customer gives dimensions (e.g. 300x250x150) -> use search_enclosures. IMPORTANT: only treat input as dimensions if it contains THREE numeric values separated by x/× or similar. A 4-digit number like "2828" is NOT a dimension — it is a size code.
- Customer mentions a product symbol or size code (e.g. "PC 2828", "EK 9 12", "ARCA 302015", "MNX 21 21 09") -> use lookup_by_symbol with that symbol to find matching products directly from the master table. Do NOT interpret size codes as dimensions.
- Customer asks to see a product range/family (e.g. "show ARCA range", "list MNX products") -> use list_products_by_group
- Customer asks about features/specs of a specific product -> use scrape_product
- Customer asks where to buy, who to contact, or how to reach Fibox in a country -> use get_contacts
- Customer gives a numeric product code (e.g. "show me 7032810", "what is 6011321") -> call lookup_product_by_code first to get the product details and Weblink, then call scrape_product with that URL. Never guess or construct a URL manually.
- Customer asks about product benefits, advantages, overview, or general Fibox info -> call list_product_docs then read_product_doc for the relevant document.
- Customer asks for accessories for a specific enclosure or product code -> follow the Accessories Lookup Workflow below.
- Customer asks for cable glands or general accessories -> call list_products_by_group with group "CABLE GLANDS" or "GENERAL ACCESSORIES" as appropriate.
- Customer attaches an image -> analyse it visually and help identify the enclosure, dimensions, or installation context.
- Customer attaches a PDF -> the text is already included in the message; use it directly to answer.

## Presenting Search Results
The search_enclosures tool returns two lists:
1. `matches` — products matching the requested W × D × H orientation.
2. `swapped_matches` — additional products matching the D × W × H orientation (width and depth swapped). These are not duplicates of the first list.

Present both lists as separate tables with columns: Symbol | Code | Dimensions | Description | Pack | Weight (kg) | Product Link
- Display in EXACTLY the order returned by the tool. Do NOT re-sort.
- Product Link: show the full URL as a clickable link. If Weblink is blank, use: https://www.fibox.com/products
- Before the swapped list, add a short note: "The following products match if you rotate the enclosure 90° (Width ↔ Depth swapped to D × W × H):"
- If `swapped_matches` is empty, do not show the swapped section at all.

After both tables, add a **Best Options** summary section. Pick the top 3–5 candidates across both lists based on closest volume match and practical fit. Format as a short bulleted list, each bullet including: Symbol, Code in backticks, dimensions, and one sentence on why it stands out (e.g. closest match, most compact, standard series, swapped orientation). Example format:
- **ARCA 302015** `8120002` — 300×200×150 mm — exact match, standard ARCA IEC cabinet with mounting plate.

**MANDATORY follow-up block after every enclosure search response.** After the Best Options section, always append this block exactly (replace the placeholder with the actual symbol of the top match):

---
> 🔩 **Need accessories for your enclosure?**
> Tell me which enclosure you selected (e.g. *ARCA 403015*) and I'll find all compatible accessories — mounting plates, DIN rail frames, inner door sets, and more.
>
> 📦 **Looking for cable glands or general accessories?**
> I can also show available cable glands and general Fibox accessories that may suit your installation.
---

## Accessories Lookup Workflow
When a customer asks for accessories for a specific enclosure (e.g. "accessories for ARCA 403015"):

**Step 1 — Find accessories in the PDF catalogs:**
1. Call `list_product_docs` to see available PDFs.
2. Call `read_product_doc` on the PDF most relevant to the product family (e.g. ARCA catalog for ARCA products).
3. In the PDF text, locate the accessories section. Accessories list the enclosures they fit in a field called "for enclosures" or inside the "description". Find all accessories where the target enclosure symbol or size group is listed.

**Step 2 — Enrich with master_web.xlsx:**
4. Call `list_products_by_group` with the product family group of the enclosure (e.g. "ARCA" for ARCA products, "TEMPO" for TEMPO products, "MNX" for MNX products) and keyword "accessories". This returns all accessory rows for that family with their Dimensions, Pack, Weight, and Weblink. Match the symbols/codes found in step 1 against this list. Always use these values — never leave Dimensions, Pack, or Weight blank if the xlsx has them.
5. Also call `list_products_by_group` with group "ACCE" (General Accessories) to retrieve items from the General Accessories catalog section that fit the enclosure.
6. Also call `list_products_by_group` with group "CABLE GLANDS" to retrieve cable glands.

**Step 3 — Present the results in this exact order:**

**Section 1** — Open with a prominent H2 heading:
- Use `## 🔩 Cabinet Specific Accessories` if the product is a cabinet (ARCA, EURONORD, SOLID)
- Use `## 🔩 Enclosure Specific Accessories` for all other product types (MNX, TEMPO, CAB, EK, etc.)
Present the enclosure/cabinet-specific accessories as a table: Symbol | Code | Dimensions | Description | Pack | Weight (kg)
- Do NOT include a Product Link column for these.
- Do NOT create custom column headings such as "Accessory Code".
- Group by category (e.g. Mounting Plates, DIN Rail, Door Accessories) using bold subheadings, sourced from the PDF structure.
- If Dimensions is blank, leave the cell empty.

**Section 2** — Add the heading `## 🔌 Cable Glands` followed by relevant cable gland items as a table: Symbol | Code | Dimensions | Description | Pack | Weight (kg)

**Section 3** — Add the heading `## 📦 General Accessories` followed by relevant ACCE items as a table: Symbol | Code | Dimensions | Description | Pack | Weight (kg)
- Items such as Ventilation devices and Inspection windows belong in this section — NEVER move them to Section 1. Their Product Group in master_web.xlsx is ACCE (General Accessories).

## Presenting Group Listings (list_products_by_group tool)
When listing any product family or group — including CABLE GLANDS and GENERAL ACCESSORIES — present results as a table with EXACTLY these columns: Symbol | Code | Dimensions | Description | Pack | Weight (kg) | Product Link
- Product Link: show the full URL as a clickable link. If Weblink is blank, use: https://www.fibox.com/products
- When listing GENERAL ACCESSORIES, always precede the table with the bold heading: **General Accessories**

## Presenting Contacts (get_contacts tool)
The tool returns `sales` (list of persons) and `distributors` (list of companies).

**Sales contacts** — present as a table: Name | Title | Email | Phone
- Skip rows where both email and phone are empty.

**Distributors** — present as a numbered list: **Company name** — website (as clickable link), email, phone.
- Only show fields that are present.
- If no distributors are returned, omit that section.

## ARCA T — New Transparent Door Range
ARCA T is Fibox's newest transparent polycarbonate door cabinet. Same footprint as ARCA IEC — but with a clear PC door that lets you inspect contents without opening. Specs: IP66, IK09, –40…+80 °C.

**ARCA T size/code table** (W × D × H mm → symbol → code):
- 200×150×150 → ARCA 203015T → 8121020
- 300×200×150 → ARCA 302015T → 8121002
- 300×300×210 → ARCA 303021T → 8121005
- 300×400×150 → ARCA 304015T → 8121022
- 300×400×210 → ARCA 304021T → 8121023
- 400×300×150 → ARCA 403015T → 8121006
- 400×300×210 → ARCA 403021T → 8121007
- 400×400×210 → ARCA 404021T → 8121009
- 400×500×210 → ARCA 405021T → 8121025
- 400×600×210 → ARCA 406021T → 8121026
- 500×400×210 → ARCA 504021T → 8121011
- 500×500×210 → ARCA 505021T → 8121032
- 600×400×210 → ARCA 604021T → 8121012

**ARCA W → ARCA T recommendation rule:** When the customer asks about ARCA W (window-cover) products or a search returns ARCA W products, check the table above for a matching ARCA T size and explicitly recommend it as an alternative with its symbol and code.

**MANDATORY footer rule:** At the end of EVERY response that mentions or lists any ARCA product (ARCA IEC, ARCA W, ARCA T, or any response where a search returns ARCA results), append exactly this block after all other content:

---
💡 **New: ARCA T — Transparent Door Range**
See inside your cabinet without opening it. ARCA T offers full IP66 protection with a clear polycarbonate door — same footprint as ARCA IEC. [Browse ARCA T →](https://www.fibox.com/products)

## Pricing
Fibox does not publish pricing. If asked: "Fibox does not publish pricing - prices vary by country and distributor. Contact your local distributor via https://www.fibox.com or reach out via their contact form."

## Tone
Professional, concise, helpful. Use markdown formatting."""

# ── USA mode — used when prompt starts with $ ─────────────────────────────────
USA_SYSTEM_PROMPT = """You are a Fibox USA product specialist. Your ONLY source of information is the live fiboxusa.com website.

## Strict Rules
- Use ONLY the scrape_product tool to fetch ALL information from fiboxusa.com
- NEVER answer from training knowledge, PDFs, internal database, or any source other than fiboxusa.com
- Show ONLY fiboxusa.com links — never fibox.com or any other domain
- If a page returns no useful content, state: "This product or information is not listed on fiboxusa.com"

## How to navigate fiboxusa.com
Start with one of these entry points based on the query, then follow product links found in the scraped page:
- Keyword / general search: https://www.fiboxusa.com/?s={search_terms_with_+_between_words}
- All enclosures: https://www.fiboxusa.com/enclosures/
- Specific product family pages you discover while scraping

## Workflow for every query
1. Choose the best starting URL on fiboxusa.com for the query
2. Call scrape_product on that URL
3. From the scraped content, identify 2–4 relevant product page links
4. Call scrape_product on each relevant product page
5. Answer based SOLELY on the scraped content — dimensions, specs, ratings, availability all come from fiboxusa.com

## Tone
Professional, concise, helpful. Use markdown formatting."""

USA_TOOLS = [
    {
        "name": "scrape_product",
        "description": "Fetch content from any fiboxusa.com page. Use this to search, browse categories, and retrieve product specifications. Always start with a relevant fiboxusa.com URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full fiboxusa.com URL to fetch"}
            },
            "required": ["url"]
        }
    }
]

TOOLS = [
    {
        "name": "search_enclosures",
        "description": "Search Fibox enclosures by dimensions (W x D x H in mm). Use ONLY when the customer provides specific measurements. Returns up to 20 matches within 20 percent tolerance, ranked by closest match.",
        "input_schema": {
            "type": "object",
            "properties": {
                "width_mm":  {"type": "number", "description": "Internal width in millimetres"},
                "depth_mm":  {"type": "number", "description": "Internal depth in millimetres"},
                "height_mm": {"type": "number", "description": "Internal height in millimetres"}
            },
            "required": ["width_mm", "depth_mm", "height_mm"]
        }
    },
    {
        "name": "list_products_by_group",
        "description": "List all products in a Fibox product family. Use when the customer asks to see a full range such as 'show ARCA range', 'list all MNX products', 'what sizes does EURONORD come in'. Do NOT use for dimension searches.",
        "input_schema": {
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "description": "Product group name such as ARCA, MNX, EURONORD, TEMPO, SOLID, CAB, EK, PICCOLO, MCE"
                },
                "category_keyword": {
                    "type": "string",
                    "description": "Optional filter keyword such as ABS, PC, Polyester"
                }
            },
            "required": ["group"]
        }
    },
    {
        "name": "scrape_product",
        "description": "Fetch product details and specs from a fibox.com product page. Use when the customer asks about features, IP rating, certifications, or detailed specs of a specific product.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full fibox.com product page URL"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "get_contacts",
        "description": "Get Fibox sales contacts and distributors for a specific country. Use whenever the customer asks where to buy, who to contact, or how to reach Fibox in a specific country.",
        "input_schema": {
            "type": "object",
            "properties": {
                "country": {
                    "type": "string",
                    "description": "Country name, e.g. Germany, Poland, UK, USA, France. Fuzzy matching is supported."
                }
            },
            "required": ["country"]
        }
    },
    {
        "name": "lookup_product_by_code",
        "description": "Look up a Fibox product by its exact numeric code (e.g. 7032810). Use when the customer mentions a specific product code. Returns the product details including the Weblink for further scraping.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The numeric product code, e.g. '7032810'"
                }
            },
            "required": ["code"]
        }
    },
    {
        "name": "list_product_docs",
        "description": "List available Fibox product PDF documents (overviews, brochures). Call this first to see what documents exist before reading one.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "read_product_doc",
        "description": "Read the full text of a Fibox product PDF document. Use when the customer asks about product features, benefits, or details that may be covered in a product overview or brochure.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "PDF filename as returned by list_product_docs"}
            },
            "required": ["filename"]
        }
    },
    {
        "name": "lookup_by_symbol",
        "description": "Look up Fibox products by symbol or size code (e.g. 'PC 2828', 'ARCA 302015', 'MNX 21 21 09'). Use when the customer mentions a product symbol or abbreviated size code. Performs partial match — 'PC 2828' will find all PC 2828 variants.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Product symbol or size code, e.g. 'PC 2828', 'ARCA 302015', 'EK 9 12'"
                }
            },
            "required": ["symbol"]
        }
    }
]


def run_script(cmd):
    try:
        result = subprocess.run(
            [sys.executable] + cmd,
            capture_output=True, text=True, timeout=60
        )
        if result.stdout.strip():
            return json.loads(result.stdout)
        return {"error": result.stderr.strip() or "Script produced no output", "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"error": "Script timed out"}
    except json.JSONDecodeError as e:
        return {"error": "Could not parse script output: " + str(e)}
    except Exception as e:
        return {"error": str(e)}


def execute_tool(name, inputs):
    if name == "search_enclosures":
        return run_script([SEARCH_PY, str(inputs["width_mm"]), str(inputs["depth_mm"]), str(inputs["height_mm"])])
    elif name == "list_products_by_group":
        cmd = [LIST_PY, inputs["group"]]
        if inputs.get("category_keyword"):
            cmd.append(inputs["category_keyword"])
        return run_script(cmd)
    elif name == "lookup_product_by_code":
        return run_script([LOOKUP_PY, inputs["code"]])
    elif name == "scrape_product":
        return run_script([SCRAPE_PY, "product", inputs["url"]])
    elif name == "get_contacts":
        return run_script([CONTACTS_PY, inputs["country"]])
    elif name == "list_product_docs":
        return run_script([READ_DOC_PY, "list"])
    elif name == "read_product_doc":
        return run_script([READ_DOC_PY, "read", inputs["filename"]])
    elif name == "lookup_by_symbol":
        return run_script([SYMBOL_PY] + inputs["symbol"].split())
    return {"error": "Unknown tool: " + name}


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        pw = request.form.get("password", "")
        matched = next((u for u, p in USERS.items() if p == pw), None)
        if matched:
            session["user"] = matched
            return redirect(url_for("index"))
        error = "Incorrect password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    if not logged_in():
        return redirect(url_for("login"))
    return render_template("index.html")


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
        else:
            return render_template("admin.html", auth=False, error="Wrong password.")
    if not session.get("admin"):
        return render_template("admin.html", auth=False, error="")
    if USE_PG:
        con = _pg(); cur = con.cursor()
        cur.execute("SELECT ts, usr, ip, status, message FROM prompts ORDER BY id DESC LIMIT 500")
        rows = cur.fetchall()
        cur.execute("SELECT usr, COUNT(*) as cnt FROM prompts GROUP BY usr ORDER BY cnt DESC")
        stats = cur.fetchall()
        con.close()
    else:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT ts, usr, ip, status, message FROM prompts ORDER BY id DESC LIMIT 500"
        ).fetchall()
        stats = con.execute(
            "SELECT usr, COUNT(*) as cnt FROM prompts GROUP BY usr ORDER BY cnt DESC"
        ).fetchall()
        con.close()
    return render_template("admin.html", auth=True, rows=rows, stats=stats)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/test")
def test_search():
    if not logged_in():
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(run_script([SEARCH_PY, "300", "250", "150"]))


@app.route("/chat", methods=["POST"])
def chat():
    if not logged_in():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data     = request.get_json()
        history  = data.get("history", [])
        user_msg = data.get("message", "").strip()

        if not user_msg:
            return jsonify({"error": "Empty message"}), 400

        ip     = request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()
        user   = session.get("user", "unknown")

        # $ prefix → USA mode: use fiboxusa.com instead of fibox.com
        use_usa = user_msg.startswith("$")
        if use_usa:
            user_msg = user_msg[1:].strip()

        row_id = log_prompt(user, ip, user_msg)

        messages = [{"role": t["role"], "content": t["content"]} for t in history]

        # Build user content — text + optional attachment
        attachment = data.get("attachment")  # {type, data (base64), media_type, name}
        if attachment:
            att_type = attachment.get("type")   # "image" or "pdf"
            att_data = attachment.get("data")   # base64 string
            raw      = base64.b64decode(att_data)
            if att_type == "image":
                user_content = [
                    {"type": "image", "source": {
                        "type": "base64",
                        "media_type": attachment.get("media_type", "image/jpeg"),
                        "data": att_data,
                    }},
                    {"type": "text", "text": user_msg or "Please describe what you see."},
                ]
            else:  # pdf — extract text and inject
                from scripts.read_doc import extract_pdf_bytes
                pdf_text = extract_pdf_bytes(raw)
                user_content = [{"type": "text",
                    "text": f"[Attached PDF: {attachment.get('name','document.pdf')}]\n\n{pdf_text}\n\n---\n{user_msg}"}]
        else:
            user_content = user_msg

        messages.append({"role": "user", "content": user_content})

        active_system = USA_SYSTEM_PROMPT if use_usa else SYSTEM_PROMPT
        active_tools  = USA_TOOLS        if use_usa else TOOLS

        language = data.get("language", "English")
        if language and language != "English":
            active_system = active_system + f"\n\nIMPORTANT: The user has selected {language} as their language. You MUST respond in {language} for all text you write."


        for _ in range(10):
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                system=active_system,
                tools=active_tools,
                messages=messages,
            )

            if response.stop_reason in ("end_turn", "max_tokens"):
                text = next(
                    (block.text for block in response.content if hasattr(block, "text")),
                    "I could not generate a response. Please try again."
                )
                update_status(row_id, "success")
                return jsonify({"response": text})

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        })
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user",      "content": tool_results})
            else:
                break

        update_status(row_id, "failed")
        return jsonify({"response": "I ran into an issue processing your request. Please try again."})

    except Exception as e:
        import traceback; traceback.print_exc()
        update_status(row_id, "failed")
        return jsonify({"error": "Server error: " + str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
