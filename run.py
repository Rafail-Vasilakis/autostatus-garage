from app import app

if __name__ == '__main__':
    print("==================================================")
    print(" AutoStatus Workshop Pro - Server Started!")
    print(" Dashboard URL: http://localhost:5000")
    print(" 10s Intake:   http://localhost:5000/intake")
    print(" AADE Registry: http://localhost:5000/aade")
    print("==================================================")
    app.run(host='0.0.0.0', port=5000, debug=True)
