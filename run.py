from app import create_app

app = create_app()

if __name__ == "__main__":
    # debug is controlled by FLASK_DEBUG in your .env file
    app.run(debug=app.config.get("DEBUG", False))
