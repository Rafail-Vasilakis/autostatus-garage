import os
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory, send_file
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from models import db, Vehicle, WorkOrder, DamageApproval, AadeRegistryLog, GarageSettings
from aade_service import AadeClient, AADE_ENDPOINTS
from backup_service import BackupService

app = Flask(__name__)
# Enable Cloudflare / Render reverse proxy HTTPS detection
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'autostatus-garage-secret-key-2026')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url or ('sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'autostatus.db'))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
db.init_app(app)

with app.app_context():
    db.create_all()

    # Seed demo garage data if database is empty
    if Vehicle.query.count() == 0:
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


@app.route('/')
def index():
    active_orders = WorkOrder.query.filter_by(is_active=True).order_by(WorkOrder.entry_time.desc()).all()
    stats = {
        'total_active': len(active_orders),
        'on_lift': sum(1 for o in active_orders if o.status == 'ON_LIFT'),
        'waiting_parts': sum(1 for o in active_orders if o.status == 'WAITING_PARTS'),
        'ready': sum(1 for o in active_orders if o.status == 'READY'),
    }
    return render_template('index.html', orders=active_orders, stats=stats)


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
    clean_plate = plate.strip().upper().replace(' ', '').replace('-', '')
    vehicles = Vehicle.query.all()
    match = None
    for v in vehicles:
        if v.plate.replace('-', '').replace(' ', '') == clean_plate:
            match = v
            break
    if match:
        return jsonify({'found': True, 'vehicle': match.to_dict()})
    return jsonify({'found': False})


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
        filename = secure_filename(f"damage_{order_id}_{uuid.uuid4().hex[:6]}.jpg")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        photo_file.save(filepath)
        photo_url = f"/static/uploads/{filename}"

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
        if 'aade_user_id' in data:
            settings.aade_user_id = data['aade_user_id'].strip()
        if 'aade_subscription_key' in data:
            settings.aade_subscription_key = data['aade_subscription_key'].strip()
        if 'aade_env' in data and data['aade_env'] in ['sandbox', 'production', 'simulation']:
            settings.aade_env = data['aade_env']

        db.session.commit()
        return jsonify({'success': True, 'settings': settings.to_dict()})

    return jsonify({'success': True, 'settings': settings.to_dict()})


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


