import os, json, subprocess, sys
from flask import Flask, request, jsonify, render_template, session, redirect, url_for
import anthropic

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "fibox-secret-key-change-in-prod")

PASSWORD = os.environ.get("APP_PASSWORD", "Fibox_agent")

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def logged_in():
    return session.get("auth") is True

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
SEARCH_PY   = os.path.join(SCRIPTS_DIR, "search_enclosures.py")
SCRAPE_PY   = os.path.join(SCRIPTS_DIR, "scrape_fibox.py")
LIST_PY     = os.path.join(SCRIPTS_DIR, "list_by_group.py")
LOOKUP_PY   = os.path.join(SCRIPTS_DIR, "lookup_by_code.py")
CONTACTS_PY = os.path.join(SCRIPTS_DIR, "contacts_lookup.py")

SYSTEM_PROMPT = """You are a Fibox product specialist assistant. Help customers find the right Fibox enclosure.

## About Fibox
Fibox is a Finnish manufacturer of polycarbonate (PC), ABS, and GRP enclosures used in industrial, electrical, and outdoor applications.

## Product Families
- NEO: Modern polycarbonate enclosures, IK10, IP66/67
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
- Customer gives dimensions (e.g. 300x250x150) -> use search_enclosures
- Customer asks to see a product range/family (e.g. "show ARCA range", "list MNX products") -> use list_products_by_group
- Customer asks about features/specs of a specific product -> use scrape_product
- Customer asks where to buy, who to contact, or how to reach Fibox in a country -> use get_contacts
- Customer gives a product code (e.g. "show me 7032810", "what is 6011321") -> call lookup_product_by_code first to get the product details and Weblink, then call scrape_product with that URL. Never guess or construct a URL manually.

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

## Presenting Contacts (get_contacts tool)
The tool returns `sales` (list of persons) and `distributors` (list of companies).

**Sales contacts** — present as a table: Name | Title | Email | Phone
- Skip rows where both email and phone are empty.

**Distributors** — present as a numbered list: **Company name** — website (as clickable link), email, phone.
- Only show fields that are present.
- If no distributors are returned, omit that section.

## Pricing
Fibox does not publish pricing. If asked: "Fibox does not publish pricing - prices vary by country and distributor. Contact your local distributor via https://www.fibox.com or reach out via their contact form."

## Tone
Professional, concise, helpful. Use markdown formatting."""

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
                    "description": "Product group name such as ARCA, MNX, EURONORD, TEMPO, NEO, SOLID, CAB, EK, PICCOLO, MCE"
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
    return {"error": "Unknown tool: " + name}


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("password") == PASSWORD:
            session["auth"] = True
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

        messages = [{"role": t["role"], "content": t["content"]} for t in history]
        messages.append({"role": "user", "content": user_msg})

        for _ in range(10):
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            if response.stop_reason in ("end_turn", "max_tokens"):
                text = next(
                    (block.text for block in response.content if hasattr(block, "text")),
                    "I could not generate a response. Please try again."
                )
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

        return jsonify({"response": "I ran into an issue processing your request. Please try again."})

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Server error: " + str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
