import os, json, subprocess, sys
from flask import Flask, request, jsonify, render_template
import anthropic

app = Flask(__name__)
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
SEARCH_PY   = os.path.join(SCRIPTS_DIR, "search_enclosures.py")
SCRAPE_PY   = os.path.join(SCRIPTS_DIR, "scrape_fibox.py")

SYSTEM_PROMPT = """You are a Fibox product specialist assistant. Help customers find the right Fibox enclosure.

## About Fibox
Fibox is a Finnish manufacturer of polycarbonate (PC), ABS, and GRP enclosures used in
industrial, electrical, and outdoor applications.

## Product Families
- NEO   : Modern polycarbonate enclosures, IK10, IP66/67
- ARCA  : Classic wall-mount cabinets, PC or steel door
- MER   : Metric series enclosures
- SOLID : Heavy-duty GRP enclosures
- ACCE  : Accessories (mounting plates, locks, etc.)
- CABLE GLANDS : Cable entries and glands (no dedicated product page)

## Dimension Convention
All dimensions are in millimetres: Width x Depth x Height.
Convert cm to mm if the customer uses centimetres (multiply by 10).

## Search Tolerance
Match products whose W, D, H each fall within +/-20% of the requested values.
Rank results by how close total volume is to the requested volume.

## Your Workflow
1. Extract W, D, H in mm from the customer query.
2. Call search_enclosures to find matching products (+/-20% tolerance).
3. Present results in a table with columns:
   Symbol | Code | Dimensions | Description | Pack | Weight (kg) | Product Link
   - Code comes from the 'code' field in the search result.
   - Product Link: show the full URL as clickable link text, e.g. [https://www.fibox.com/products/...](https://www.fibox.com/products/...)
   - If Weblink is blank, write: https://www.fibox.com/products (general catalogue).
   - Display results in EXACTLY the order returned by the tool. Do NOT re-sort.
   - Results with exact_dims >= 2 are the closest matches (2+ individual dimensions match the request closely). Label that group "Closest Matches". Remaining results go under "Other Close Matches".
   - MCE products are always shown last.
4. If the customer asks for product benefits/features, call scrape_product with the URL.
5. If the customer asks where to buy, call scrape_distributors with the country name.
   Present the distributor info but do NOT proactively offer it unprompted.

## Pricing
Fibox does not publish pricing. If asked, reply:
"Fibox does not publish pricing — prices vary by country, distributor, and order volume.
Contact your local distributor via https://www.fibox.com (Sales Network / Where to Buy),
or reach out to Fibox directly through their contact form. Have the product code ready."
Do NOT proactively mention pricing or suggest the customer ask about it.

## URL Coverage
82% of products have a Weblink. Accessories and cable glands may have a blank Weblink —
in that case, direct the customer to https://www.fibox.com/products.

## Tone
Professional, concise, and helpful. Always mention if no exact match was found and explain
the closest alternatives clearly. Format responses with markdown for readability.
Do NOT end responses by offering to provide pricing or distributor info unprompted."""

TOOLS = [
    {
        "name": "search_enclosures",
        "description": (
            "Search the Fibox product catalogue for enclosures matching the requested dimensions. "
            "Applies ±20% tolerance on each dimension and returns up to 20 results ranked by "
            "how close the volume is to the requested volume. Each result includes the product "
            "symbol, dimensions, description, packing unit, weight, and product page URL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "width_mm":  {"type": "number", "description": "Required internal width in millimetres"},
                "depth_mm":  {"type": "number", "description": "Required internal depth in millimetres"},
                "height_mm": {"type": "number", "description": "Required internal height in millimetres"},
            },
            "required": ["width_mm", "depth_mm", "height_mm"],
        },
    },
    {
        "name": "scrape_product",
        "description": (
            "Fetch product details, key features, and technical specifications from a "
            "fibox.com product page. Use when the customer asks about benefits, certifications, "
            "IP rating, or detailed specs of a specific product."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full fibox.com product page URL"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "scrape_distributors",
        "description": (
            "Get Fibox distributor and dealer information from fibox.com, optionally filtered "
            "by country. Use when the customer asks where to buy Fibox products."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "country": {
                    "type": "string",
                    "description": "Country name to filter results (e.g. 'Germany', 'Poland'). Leave empty for all countries.",
                },
            },
            "required": [],
        },
    },
]


def run_script(cmd):
    """Run a Python script and return parsed JSON output."""
    try:
        result = subprocess.run(
            [sys.executable] + cmd,
            capture_output=True, text=True, timeout=60
        )
        if result.stdout.strip():
            return json.loads(result.stdout)
        else:
            return {"error": result.stderr.strip() or "Script produced no output", "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"error": "Script timed out after 60 seconds"}
    except json.JSONDecodeError as e:
        return {"error": f"Could not parse script output: {e}", "raw": result.stdout[:500]}
    except Exception as e:
        return {"error": str(e)}


def execute_tool(name, inputs):
    """Dispatch a tool call to the appropriate script."""
    if name == "search_enclosures":
        return run_script([
            SEARCH_PY,
            str(inputs["width_mm"]),
            str(inputs["depth_mm"]),
            str(inputs["height_mm"]),
        ])
    elif name == "scrape_product":
        return run_script([SCRAPE_PY, "product", inputs["url"]])
    elif name == "scrape_distributors":
        country = inputs.get("country", "")
        cmd = [SCRAPE_PY, "distributors"]
        if country:
            cmd.append(country)
        return run_script(cmd)
    return {"error": f"Unknown tool: {name}"}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data     = request.get_json()
    history  = data.get("history", [])   # list of {role, content} from the frontend
    user_msg = data.get("message", "").strip()

    if not user_msg:
        return jsonify({"error": "Empty message"}), 400

    # Build message list from history + new user message
    messages = []
    for turn in history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": user_msg})

    # Agentic loop — keep going until Claude stops calling tools
    for _ in range(10):   # safety limit
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            # Extract final text response
            text = next(
                (block.text for block in response.content if hasattr(block, "text")),
                "I couldn't generate a response. Please try again."
            )
            return jsonify({"response": text})

        if response.stop_reason == "tool_use":
            # Execute all requested tools
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })

            # Add assistant turn + tool results and loop
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user",      "content": tool_results})
        else:
            break

    return jsonify({"response": "I ran into an issue processing your request. Please try again."})


@app.route("/test")
def test_search():
    """Diagnostic: run search_enclosures with fixed dims and return raw result."""
    result = run_script([SEARCH_PY, "300", "250", "150"])
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
