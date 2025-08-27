from flask import Flask
from routes.core import bp as core_bp
from routes.debug import bp as debug_bp

# I'm bootstrapping the Flask app here so future me remembers where it all starts.
app = Flask(__name__)
# Keeping the main routes wired up; don't forget these if things vanish.
app.register_blueprint(core_bp)
# Debug routes live here for when I need to poke around.
app.register_blueprint(debug_bp)

if __name__ == "__main__":
    # Running the dev server directly because that's how I like to test.
    app.run(host="0.0.0.0", port=5000)
