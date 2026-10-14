import os
import uuid
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory, send_file, session, make_response
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from models import db, Vehicle, WorkOrder, DamageApproval, AadeRegistryLog, GarageSettings
from aade_service import AadeClient, AADE_ENDPOINTS
from backup_service import BackupService
from greek_vehicle_service import GreekVehicleService
from registration_doc_service import RegistrationDocService

app = Flask(__name__)
app.config['APP_VERSION'] = '2.1.0'
# Enable Render reverse proxy HTTPS detection
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'autostatus-garage-secret-key-2026')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url or ('sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'autostatus.db'))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=90)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
db.init_app(app)

with app.app_context():
    db.create_all()

    # Safe column migration for existing databases
    try:
        from sqlalchemy import text
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE garage_settings ADD COLUMN workshop_pin VARCHAR(20) DEFAULT '1234'"))
            conn.commit()
    except Exception:
        pass

    try:
        from sqlalchemy import text
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE garage_settings ADD COLUMN device_secret VARCHAR(64) DEFAULT 'garage_terminal_sec_2026'"))
            conn.commit()
    except Exception:
        pass

    for col, col_def in [
        ('phone', "VARCHAR(30) DEFAULT '210 285 1201'"),
        ('address', "VARCHAR(150) DEFAULT 'Αθήνα, Ελλάδα'"),
        ('email', "VARCHAR(100) DEFAULT 'info@garage.gr'"),
        ('quick_services', "VARCHAR(255) DEFAULT 'Τακτικό Service / Λάδια,Φρένα / Τακάκια,Διαγνωστικός Έλεγχος,Προετοιμασία ΚΤΕΟ'")
    ]:
        try:
            from sqlalchemy import text
            with db.engine.connect() as conn:
                conn.execute(text(f"ALTER TABLE garage_settings ADD COLUMN {col} {col_def}"))
                conn.commit()
        except Exception:
            pass

    # Seed demo garage data if database is empty and demo seeding is enabled
    seed_demo = os.environ.get('SEED_DEMO_DATA', 'true').lower() in ['true', '1', 'yes']
    if seed_demo and Vehicle.query.count() == 0:
        v1 = Vehicle(plate="ΥΝΕ-5421", owner_name="Γιώργος Παπαδόπουλος", owner_phone="6971234567", car_model="Toyota Yaris 1.3")
        v2 = Vehicle(plate="ΙΒΧ-8902", owner_name="Ελένη Κωνσταντίνου", owner_phone="6987654321", car_model="Volkswagen Golf 1.4 TSI")
        v3 = Vehicle(plate="ΖΗΤ-3310", owner_name="Δημήτρης Αντωνίου", owner_phone="6945551234", car_model="Ford Focus 1.6")
        db.session.add_all([v1, v2, v3])
        db.session.commit()

        # Active work orders
        wo1 = WorkOrder(vehicle=v1, status="ON_LIFT", service_type="Τακτικό Service (Λάδια, Φίλτρα)", initial_cost=95.0, notes="Έλεγχος και μπροστινά φρένα")
        wo2 = WorkOrder(vehicle=v2, status="WAITING_PARTS", service_type="Αλλαγή Ιμάντα & Αντλίας Νερού", initial_cost=220.0, notes="Αναμονή αντλίας Gates")
        wo3 = WorkOrder(vehicle=v3, status="READY", service_type="Αλλαγή Τακάκια & Υγρά Φρένων", initial_cost=75.0, notes="Έτοιμο για παραλαβή")
        db.session.add_all([wo1, wo2, wo3])
        db.session.commit()

        # Damage approval request for wo1
        dam1 = DamageApproval(
            work_order=wo1,
            photo_url="/static/img/brake_disc_demo.jpg",
            description="Έντονη φθορά και αυλάκωση σε μπροστινές δισκόπλακες",
            extra_cost=85.0,
            status="PENDING"
        )
        db.session.add(dam1)

        # AADE logs
        for wo in [wo1, wo2, wo3]:
            aade = AadeRegistryLog(
                work_order=wo,
                plate=wo.vehicle.plate,
                owner_name=wo.vehicle.owner_name,
                entry_timestamp=wo.entry_time,
                service_type=wo.service_type,
                aade_uid=f"AADE-2026-{uuid.uuid4().hex[:8].upper()}",
                aade_mark=f"800{uuid.uuid4().int % 1000000000}",
                sync_status="SANDBOX_OK",
                submission_mode="sandbox"
            )
            db.session.add(aade)
        db.session.commit()

    # Ensure Garage Settings exist
    GarageSettings.get_settings()


@app.context_processor
def inject_settings():
    settings = GarageSettings.get_settings()
    return dict(aade_settings=settings)


def is_workshop_device():
    settings = GarageSettings.get_settings()
    current_sec = settings.device_secret or "garage_terminal_sec_2026"
    expected_cookie = f"terminal_{current_sec}"
    return (session.get('workshop_authenticated') == current_sec) or (request.cookies.get('workshop_device') == expected_cookie)


@app.before_request
def require_workshop_auth():
    # Enforce HTTPS on Render
    if request.headers.get('X-Forwarded-Proto') == 'http' and not request.host.startswith(('localhost', '127.0.0.1')):
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url, code=301)

    # Public endpoints open to customers & external visitors
    exempt_endpoints = {'static', 'index', 'customer_lookup', 'track', 'decide_approval', 'workshop_view', 'logout_view'}
    if request.endpoint in exempt_endpoints or (request.endpoint is None and request.path.startswith('/static/')):
        return None

    # Check if the device is verified as a workshop terminal
    if is_workshop_device():
        settings = GarageSettings.get_settings()
        session['workshop_authenticated'] = settings.device_secret or "garage_terminal_sec_2026"
        return None

    # Unauthorized access to management routes
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': 'Πρόσβαση μόνο για το προσωπικό του συνεργείου', 'auth_required': True}), 401
    return redirect(url_for('workshop_view'))


@app.route('/')
def index():
    settings = GarageSettings.get_settings()
    current_sec = settings.device_secret or "garage_terminal_sec_2026"

    # Authorized staff sees workshop lifts & management dashboard
    if is_workshop_device():
        session['workshop_authenticated'] = current_sec
        active_orders = WorkOrder.query.filter_by(is_active=True).order_by(WorkOrder.entry_time.desc()).all()
        stats = {
            'total_active': len(active_orders),
            'on_lift': sum(1 for o in active_orders if o.status == 'ON_LIFT'),
            'waiting_parts': sum(1 for o in active_orders if o.status == 'WAITING_PARTS'),
            'ready': sum(1 for o in active_orders if o.status == 'READY'),
        }
        resp = make_response(render_template('index.html', orders=active_orders, stats=stats))
        # Keep device authenticated for 1 full year without requiring PIN
        resp.set_cookie('workshop_device', f"terminal_{current_sec}", max_age=365*24*3600, httponly=True, samesite='Lax')
        return resp

    # Customers, guests, and strangers see ONLY the Customer Tracking Portal
    return render_template('customer_portal.html')


@app.route('/workshop', methods=['GET', 'POST'])
def workshop_view():
    """
    Workshop Device Pairing:
    - If already paired, enters dashboard directly.
    - If not paired, requires the 4-digit PIN once.
    - Once paired, saves 1-year cryptographic token so PIN is never asked again on this device.
    """
    settings = GarageSettings.get_settings()
    current_sec = settings.device_secret or "garage_terminal_sec_2026"
    expected_cookie = f"terminal_{current_sec}"

    # 1. If already paired, go straight to dashboard
    if is_workshop_device():
        session['workshop_authenticated'] = current_sec
        return redirect(url_for('index'))

    # 2. Check for secret master URL parameter (Magic link, e.g. /workshop?key=...)
    master_key = request.args.get('key', '').strip()
    if master_key and (master_key == current_sec or master_key == settings.workshop_pin):
        session['workshop_authenticated'] = current_sec
        resp = redirect(url_for('index'))
        resp.set_cookie('workshop_device', expected_cookie, max_age=365*24*3600, httponly=True, samesite='Lax')
        return resp

    # 3. Handle PIN verification
    if request.method == 'POST':
        pin = ''
        if request.is_json and request.json:
            pin = str(request.json.get('pin', '')).strip()
        else:
            pin = str(request.form.get('pin', '')).strip()

        expected_pin = str(settings.workshop_pin or '1234').strip()
        if pin and pin == expected_pin:
            session['workshop_authenticated'] = current_sec
            if request.is_json:
                resp = jsonify({'success': True, 'redirect': url_for('index')})
            else:
                resp = redirect(url_for('index'))
            resp.set_cookie('workshop_device', expected_cookie, max_age=365*24*3600, httponly=True, samesite='Lax')
            return resp
        else:
            if request.is_json:
                return jsonify({'success': False, 'error': 'Λανθασμένο PIN συνεργείου'}), 401
            return render_template('workshop_pair.html', error_message='Λανθασμένο PIN συνεργείου. Δοκιμάστε ξανά.')

    # 4. Render one-time device enrollment screen
    return render_template('workshop_pair.html')


@app.route('/customer-lookup', methods=['POST'])
def customer_lookup():
    """
    Allows a customer on the public homepage to look up their car by plate.
    Redirects strictly to their own work order tracking page without revealing other cars.
    """
    raw_plate = request.form.get('plate', '').strip()
    clean_target = GreekVehicleService.normalize_plate(raw_plate).replace('-', '').replace(' ', '')

    active_orders = WorkOrder.query.filter_by(is_active=True).all()
    for wo in active_orders:
        if wo.vehicle.plate.replace('-', '').replace(' ', '') == clean_target:
            return redirect(url_for('track', token=wo.token))

    return render_template('customer_portal.html', error_message=f"Δεν βρέθηκε ενεργή επισκευή για την πινακίδα '{raw_plate}'. Παρακαλώ ελέγξτε την πινακίδα ή καλέστε το συνεργείο.")


@app.route('/logout')
def logout_view():
    session.clear()
    resp = redirect(url_for('index', logged_out='1'))
    resp.delete_cookie('workshop_device', path='/', samesite='Lax')
    resp.set_cookie('workshop_device', '', expires=0, max_age=0, path='/', httponly=True, samesite='Lax')
    return resp


@app.route('/api/settings/reset_devices', methods=['POST'])
def api_reset_devices():
    settings = GarageSettings.get_settings()
    settings.reset_devices()
    session.clear()
    resp = jsonify({
        'success': True,
        'message': 'Όλες οι συσκευές αποσυνδέθηκαν επιτυχώς!',
        'redirect': url_for('workshop_view', reset='1')
    })
    resp.delete_cookie('workshop_device', path='/', samesite='Lax')
    resp.set_cookie('workshop_device', '', expires=0, max_age=0, path='/', httponly=True, samesite='Lax')
    return resp


@app.route('/intake', methods=['GET', 'POST'])
def intake():
    if request.method == 'POST':
        raw_plate = request.form.get('plate', '').strip().upper().replace(' ', '').replace('-', '')
        # Format plate e.g. YNE-5421
        if len(raw_plate) == 7 and raw_plate[:3].isalpha() and raw_plate[3:].isdigit():
            plate = f"{raw_plate[:3]}-{raw_plate[3:]}"
        else:
            plate = raw_plate

        owner_name = request.form.get('owner_name', '').strip()
        owner_phone = request.form.get('owner_phone', '').strip().replace(' ', '')
        car_model = request.form.get('car_model', '').strip()
        service_type = request.form.get('service_type', 'Γενικό Service').strip()
        initial_cost = float(request.form.get('initial_cost', 0) or 0)
        notes = request.form.get('notes', '').strip()

        # Find or create vehicle
        vehicle = Vehicle.query.filter_by(plate=plate).first()
        if not vehicle:
            vehicle = Vehicle(
                plate=plate,
                owner_name=owner_name,
                owner_phone=owner_phone,
                car_model=car_model,
                last_service_date=datetime.utcnow()
            )
            db.session.add(vehicle)
        else:
            # Update info
            if owner_name: vehicle.owner_name = owner_name
            if owner_phone: vehicle.owner_phone = owner_phone
            if car_model: vehicle.car_model = car_model
            vehicle.last_service_date = datetime.utcnow()

        db.session.commit()

        # Create WorkOrder
        wo = WorkOrder(
            vehicle=vehicle,
            status='ON_LIFT',
            service_type=service_type,
            initial_cost=initial_cost,
            notes=notes,
            entry_time=datetime.utcnow(),
            is_active=True
        )
        db.session.add(wo)
        db.session.commit()

        # Real-time sync with AADE Digital Registry (Α.1057/2025)
        settings = GarageSettings.get_settings()
        client = AadeClient(
            user_id=settings.aade_user_id,
            subscription_key=settings.aade_subscription_key,
            vat_number=settings.vat_number,
            env=settings.aade_env
        )
        aade_res = client.transmit_vehicle_entry(
            plate=vehicle.plate,
            owner_name=vehicle.owner_name,
            owner_phone=vehicle.owner_phone,
            service_type=wo.service_type,
            order_id=wo.id,
            entry_time=wo.entry_time
        )

        aade_log = AadeRegistryLog(
            work_order=wo,
            plate=vehicle.plate,
            owner_name=vehicle.owner_name,
            entry_timestamp=wo.entry_time,
            service_type=wo.service_type,
            aade_uid=aade_res['aade_uid'],
            aade_mark=aade_res.get('aade_mark'),
            sync_status=aade_res['sync_status'],
            submission_mode=aade_res['submission_mode'],
            raw_response=aade_res.get('raw_response')
        )
        db.session.add(aade_log)
        db.session.commit()

        return redirect(url_for('index', new_order=wo.id))

    return render_template('intake.html')


@app.route('/api/plate_lookup/<plate>')
def plate_lookup(plate):
    clean_plate = GreekVehicleService.normalize_plate(plate)
    clean_no_hyphen = clean_plate.replace('-', '').replace(' ', '')

    vehicles = Vehicle.query.all()
    match = None
    for v in vehicles:
        if v.plate.replace('-', '').replace(' ', '') == clean_no_hyphen:
            match = v
            break

    res = GreekVehicleService.lookup_vehicle(clean_plate, db_vehicle=match)
    return jsonify(res)


@app.route('/api/aade/afm_lookup/<afm>')
def api_afm_lookup(afm):
    res = RegistrationDocService.lookup_afm_data(afm)
    return jsonify(res)


@app.route('/api/ocr/parse_registration', methods=['POST'])
def api_parse_registration():
    data = request.json or {}
    raw_text = data.get('text', '')
    res = RegistrationDocService.parse_ocr_text(raw_text)
    return jsonify(res)


@app.route('/api/work_orders/<int:order_id>/status', methods=['POST'])
def update_status(order_id):
    wo = WorkOrder.query.get_or_404(order_id)
    new_status = request.json.get('status')
    if new_status in ['ON_LIFT', 'WAITING_PARTS', 'READY', 'DELIVERED']:
        wo.status = new_status
        if new_status == 'DELIVERED':
            wo.exit_time = datetime.utcnow()
            wo.is_active = False
            if wo.aade_log:
                wo.aade_log.exit_timestamp = wo.exit_time
        db.session.commit()
        return jsonify({'success': True, 'order': wo.to_dict()})
    return jsonify({'success': False, 'error': 'Invalid status'}), 400


@app.route('/api/work_orders/<int:order_id>/add_damage', methods=['POST'])
def add_damage(order_id):
    wo = WorkOrder.query.get_or_404(order_id)
    description = request.form.get('description', '').strip()
    extra_cost = float(request.form.get('extra_cost', 0) or 0)
    photo_file = request.files.get('photo')

    photo_url = "/static/img/brake_disc_demo.jpg"  # default fallback
    if photo_file and photo_file.filename != '':
        try:
            from PIL import Image
            img = Image.open(photo_file.stream)
            img.verify()
            photo_file.seek(0)

            filename = secure_filename(f"damage_{order_id}_{uuid.uuid4().hex[:6]}.jpg")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            photo_file.save(filepath)
            photo_url = f"/static/uploads/{filename}"
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid image file. Only valid images allowed.'}), 400

    dam = DamageApproval(
        work_order=wo,
        photo_url=photo_url,
        description=description,
        extra_cost=extra_cost,
        status='PENDING'
    )
    db.session.add(dam)
    db.session.commit()

    return jsonify({'success': True, 'approval_id': dam.id, 'token': wo.token})


@app.route('/track/<token>')
def track(token):
    wo = WorkOrder.query.filter_by(token=token).first_or_404()
    return render_template('track.html', order=wo)


@app.route('/api/approvals/<int:approval_id>/decide', methods=['POST'])
def decide_approval(approval_id):
    approval = DamageApproval.query.get_or_404(approval_id)
    req_token = request.json.get('token') if request.json else None

    # IDOR Check: Ensure request token matches the work order
    if not req_token or req_token != approval.work_order.token:
        return jsonify({'success': False, 'error': 'Unauthorized: invalid order token'}), 403

    decision = request.json.get('decision')  # 'APPROVED' or 'REJECTED'
    if decision in ['APPROVED', 'REJECTED']:
        approval.status = decision
        approval.decision_time = datetime.utcnow()
        db.session.commit()
        return jsonify({
            'success': True,
            'status': approval.status,
            'new_total': approval.work_order.total_cost
        })
    return jsonify({'success': False, 'error': 'Invalid decision'}), 400


@app.route('/api/work_orders/<int:order_id>/delete', methods=['POST'])
def delete_work_order(order_id):
    wo = WorkOrder.query.get_or_404(order_id)
    db.session.delete(wo)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/vehicles/<int:vehicle_id>/delete', methods=['POST'])
def delete_vehicle(vehicle_id):
    v = Vehicle.query.get_or_404(vehicle_id)
    db.session.delete(v)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/aade')
def aade_registry():
    logs = AadeRegistryLog.query.order_by(AadeRegistryLog.entry_timestamp.desc()).all()
    settings = GarageSettings.get_settings()
    return render_template('aade_log.html', logs=logs, settings=settings)


@app.route('/settings')
@app.route('/settings/aade')
def settings_view():
    settings = GarageSettings.get_settings()
    return render_template('settings.html', settings=settings)


@app.route('/api/settings/aade', methods=['GET', 'POST'])
def api_aade_settings():
    settings = GarageSettings.get_settings()
    if request.method == 'POST':
        data = request.json or request.form
        if 'garage_name' in data:
            settings.garage_name = data['garage_name'].strip()
        if 'vat_number' in data:
            settings.vat_number = data['vat_number'].strip()
        if 'phone' in data:
            settings.phone = data['phone'].strip()
        if 'address' in data:
            settings.address = data['address'].strip()
        if 'email' in data:
            settings.email = data['email'].strip()
        if 'quick_services' in data:
            settings.quick_services = data['quick_services'].strip()
        if 'aade_user_id' in data:
            settings.aade_user_id = data['aade_user_id'].strip()
        if 'aade_subscription_key' in data:
            settings.aade_subscription_key = data['aade_subscription_key'].strip()
        if 'aade_env' in data and data['aade_env'] in ['sandbox', 'production', 'simulation']:
            settings.aade_env = data['aade_env']
        if 'workshop_pin' in data and data['workshop_pin'].strip():
            settings.workshop_pin = data['workshop_pin'].strip()

        db.session.commit()
        return jsonify({'success': True, 'settings': settings.to_dict()})

    return jsonify({'success': True, 'settings': settings.to_dict()})


@app.route('/api/settings/clear_demo_data', methods=['POST'])
def api_clear_demo_data():
    DamageApproval.query.delete()
    AadeRegistryLog.query.delete()
    WorkOrder.query.delete()
    Vehicle.query.delete()
    db.session.commit()
    return jsonify({'success': True, 'message': 'Όλα τα δοκιμαστικά δεδομένα εκκαθαρίστηκαν επιτυχώς!'})


@app.route('/api/settings/aade/test', methods=['POST'])
def api_test_aade_connection():
    settings = GarageSettings.get_settings()
    data = request.json or {}

    user_id = data.get('aade_user_id', settings.aade_user_id)
    subscription_key = data.get('aade_subscription_key', settings.aade_subscription_key)
    vat_number = data.get('vat_number', settings.vat_number)
    env = data.get('aade_env', settings.aade_env)

    client = AadeClient(
        user_id=user_id,
        subscription_key=subscription_key,
        vat_number=vat_number,
        env=env
    )

    test_result = client.test_connection()

    # Update settings status in DB
    settings.last_test_date = datetime.utcnow()
    settings.last_test_status = 'CONNECTED' if test_result['success'] else 'FAILED'
    settings.last_test_message = test_result['message']
    db.session.commit()

    return jsonify({
        'success': test_result['success'],
        'status_code': test_result['status_code'],
        'mode': test_result['mode'],
        'message': test_result['message'],
        'last_test_date': settings.last_test_date.strftime('%d/%m/%Y %H:%M:%S')
    })


@app.route('/api/aade/resync/<int:log_id>', methods=['POST'])
def api_resync_aade(log_id):
    log = AadeRegistryLog.query.get_or_404(log_id)
    settings = GarageSettings.get_settings()
    client = AadeClient(
        user_id=settings.aade_user_id,
        subscription_key=settings.aade_subscription_key,
        vat_number=settings.vat_number,
        env=settings.aade_env
    )

    res = client.transmit_vehicle_entry(
        plate=log.plate,
        owner_name=log.owner_name,
        owner_phone=log.work_order.vehicle.owner_phone if log.work_order else '',
        service_type=log.service_type,
        order_id=log.work_order_id,
        entry_time=log.entry_timestamp
    )

    log.aade_uid = res['aade_uid']
    if res.get('aade_mark'):
        log.aade_mark = res['aade_mark']
    log.sync_status = res['sync_status']
    log.submission_mode = res['submission_mode']
    log.raw_response = res.get('raw_response')
    db.session.commit()

    return jsonify({'success': res['success'], 'log': log.to_dict()})



@app.route('/api/messages/<int:order_id>')
def generate_message(order_id):
    from urllib.parse import quote
    wo = WorkOrder.query.get_or_404(order_id)
    base_url = request.host_url.rstrip('/')
    tracking_link = f"{base_url}/track/{wo.token}"

    raw_phone = wo.vehicle.owner_phone.replace(' ', '').replace('-', '').replace('+30', '').replace('0030', '')
    # Standardize Greek mobile phone format
    if len(raw_phone) == 10 and raw_phone.startswith('69'):
        wa_phone = f"30{raw_phone}"
        sms_phone = raw_phone
    else:
        wa_phone = raw_phone
        sms_phone = raw_phone

    if wo.status == 'READY':
        text = f"Καλημέρα σας από το Συνεργείο! Το όχημά σας ({wo.vehicle.plate}) είναι ΕΤΟΙΜΟ προς παραλαβή. Τελικό σύνολο: {wo.total_cost}€. Αναλυτική κάρτα εργασίας: {tracking_link}"
    elif any(a.status == 'PENDING' for a in wo.approvals):
        pending = [a for a in wo.approvals if a.status == 'PENDING'][0]
        text = f"Συνεργείο: Εντοπίστηκε ανάγκη αντικατάστασης στο όχημά σας ({wo.vehicle.plate}): {pending.description} (+{pending.extra_cost}€). Δείτε τη φωτογραφία και εγκρίνετε άμεσα εδώ: {tracking_link}"
    else:
        text = f"Συνεργείο: Το όχημά σας ({wo.vehicle.plate}) βρίσκεται: {wo.status_label_el}. Παρακολουθήστε ζωντανά την εξέλιξη: {tracking_link}"

    encoded_text = quote(text)
    viber_link = f"viber://forward?text={encoded_text}"
    whatsapp_link = f"https://wa.me/{wa_phone}?text={encoded_text}"
    sms_link = f"sms:{sms_phone}?body={encoded_text}"

    return jsonify({
        'phone': wo.vehicle.owner_phone,
        'clean_phone': sms_phone,
        'text': text,
        'tracking_link': tracking_link,
        'viber_link': viber_link,
        'whatsapp_link': whatsapp_link,
        'sms_link': sms_link
    })


@app.route('/settings/backup')
def backup_settings_view():
    backups = BackupService.list_backups()
    settings = GarageSettings.get_settings()
    return render_template('backup.html', backups=backups, settings=settings)


@app.route('/api/backup/create', methods=['POST'])
def api_create_backup():
    db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'autostatus.db')
    uploads_path = app.config['UPLOAD_FOLDER']
    res = BackupService.create_backup(db_path, uploads_path)
    return jsonify({'success': True, 'backup': res})


@app.route('/api/backup/download/<filename>')
def api_download_backup(filename):
    safe_name = secure_filename(filename)
    filepath = os.path.join(BackupService.get_backup_dir(), safe_name)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Backup file not found'}), 404
    return send_file(filepath, as_attachment=True, download_name=safe_name)


@app.route('/api/backup/restore', methods=['POST'])
def api_restore_backup():
    file = request.files.get('backup_file')
    if not file or file.filename == '':
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400

    filename = secure_filename(file.filename)
    if not filename.endswith('.zip'):
        return jsonify({'success': False, 'error': 'Only .zip files supported'}), 400

    upload_path = os.path.join(BackupService.get_backup_dir(), f"uploaded_{filename}")
    file.save(upload_path)

    db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'autostatus.db')
    uploads_path = app.config['UPLOAD_FOLDER']

    try:
        BackupService.restore_backup(upload_path, db_path, uploads_path)
        return jsonify({'success': True, 'message': 'Backup restored successfully!'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)


