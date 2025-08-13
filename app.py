from flask import Flask
from routes.core import bp as core_bp
from routes.debug import bp as debug_bp

app = Flask(__name__)
app.register_blueprint(core_bp)
app.register_blueprint(debug_bp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
