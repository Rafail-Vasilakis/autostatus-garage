import uuid
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Vehicle(db.Model):
    __tablename__ = 'vehicles'
    id = db.Column(db.Integer, primary_key=True)
    plate = db.Column(db.String(20), unique=True, nullable=False, index=True)
    owner_name = db.Column(db.String(120), nullable=False)
    owner_phone = db.Column(db.String(30), nullable=False)
    car_model = db.Column(db.String(100), nullable=True)
    last_service_date = db.Column(db.DateTime, default=datetime.utcnow)
    last_mileage = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    work_orders = db.relationship('WorkOrder', backref='vehicle', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'plate': self.plate,
            'owner_name': self.owner_name,
            'owner_phone': self.owner_phone,
            'car_model': self.car_model or '',
            'last_service_date': self.last_service_date.strftime('%Y-%m-%d') if self.last_service_date else '',
            'last_mileage': self.last_mileage or 0
        }

class WorkOrder(db.Model):
    __tablename__ = 'work_orders'
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False)
    status = db.Column(db.String(30), default='ON_LIFT')  # ON_LIFT, WAITING_PARTS, READY, DELIVERED
    service_type = db.Column(db.String(150), default='Γενικό Service & Έλεγχος')
    notes = db.Column(db.Text, nullable=True)
    initial_cost = db.Column(db.Float, default=0.0)
    token = db.Column(db.String(64), unique=True, default=lambda: uuid.uuid4().hex[:12], index=True)
    entry_time = db.Column(db.DateTime, default=datetime.utcnow)
    exit_time = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)

    approvals = db.relationship('DamageApproval', backref='work_order', lazy=True, cascade="all, delete-orphan")
    aade_log = db.relationship('AadeRegistryLog', backref='work_order', uselist=False, cascade="all, delete-orphan")

    @property
    def approved_extra_cost(self):
        return sum(a.extra_cost for a in self.approvals if a.status == 'APPROVED')

    @property
    def total_cost(self):
        return round((self.initial_cost or 0.0) + self.approved_extra_cost, 2)

    @property
    def status_label_el(self):
        labels = {
            'ON_LIFT': 'Στη Ράμπα',
            'WAITING_PARTS': 'Αναμονή Ανταλλακτικών',
            'READY': 'Έτοιμο προς Παραλαβή',
            'DELIVERED': 'Παραδόθηκε'
        }
        return labels.get(self.status, self.status)

    def to_dict(self):
        return {
            'id': self.id,
            'plate': self.vehicle.plate,
            'owner_name': self.vehicle.owner_name,
            'owner_phone': self.vehicle.owner_phone,
            'car_model': self.vehicle.car_model or '',
            'status': self.status,
            'status_label': self.status_label_el,
            'service_type': self.service_type,
            'notes': self.notes or '',
            'initial_cost': self.initial_cost,
            'approved_extra_cost': self.approved_extra_cost,
            'total_cost': self.total_cost,
            'token': self.token,
            'entry_time': self.entry_time.strftime('%H:%M - %d/%m'),
            'exit_time': self.exit_time.strftime('%H:%M - %d/%m') if self.exit_time else None,
            'is_active': self.is_active,
            'approvals': [a.to_dict() for a in self.approvals]
        }

class DamageApproval(db.Model):
    __tablename__ = 'damage_approvals'
    id = db.Column(db.Integer, primary_key=True)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_orders.id'), nullable=False)
    photo_url = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    extra_cost = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='PENDING')  # PENDING, APPROVED, REJECTED
    decision_time = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'photo_url': self.photo_url,
            'description': self.description,
            'extra_cost': self.extra_cost,
            'status': self.status,
            'created_at': self.created_at.strftime('%H:%M - %d/%m'),
            'decision_time': self.decision_time.strftime('%H:%M - %d/%m') if self.decision_time else None
        }

class AadeRegistryLog(db.Model):
    __tablename__ = 'aade_registry_logs'
    id = db.Column(db.Integer, primary_key=True)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_orders.id'), nullable=False)
    plate = db.Column(db.String(20), nullable=False)
    owner_name = db.Column(db.String(120), nullable=False)
    entry_timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    exit_timestamp = db.Column(db.DateTime, nullable=True)
    service_type = db.Column(db.String(150), nullable=False)
    aade_uid = db.Column(db.String(64), nullable=False, unique=True)
    aade_mark = db.Column(db.String(64), nullable=True)
    sync_status = db.Column(db.String(30), default='SYNCED')  # SYNCED_LIVE, SANDBOX_OK, SIMULATED_TEST, OFFLINE_QUEUED
    submission_mode = db.Column(db.String(30), default='sandbox')  # sandbox, production, simulation
    raw_response = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'plate': self.plate,
            'owner_name': self.owner_name,
            'entry_timestamp': self.entry_timestamp.strftime('%d/%m/%Y %H:%M:%S'),
            'exit_timestamp': self.exit_timestamp.strftime('%d/%m/%Y %H:%M:%S') if self.exit_timestamp else 'Εντός Συνεργείου',
            'service_type': self.service_type,
            'aade_uid': self.aade_uid,
            'aade_mark': self.aade_mark or '-',
            'sync_status': self.sync_status,
            'submission_mode': self.submission_mode
        }


class GarageSettings(db.Model):
    __tablename__ = 'garage_settings'
    id = db.Column(db.Integer, primary_key=True)
    garage_name = db.Column(db.String(120), default="Συνεργείο Αυτοκινήτων - Garage Pro")
    vat_number = db.Column(db.String(20), default="094123456")
    phone = db.Column(db.String(30), default="210 285 1201")
    address = db.Column(db.String(150), default="Αθήνα, Ελλάδα")
    email = db.Column(db.String(100), default="info@garage.gr")
    aade_user_id = db.Column(db.String(100), default="workshop_test_user")
    aade_subscription_key = db.Column(db.String(150), default="demo_key_sub_2026")
    aade_env = db.Column(db.String(20), default="sandbox")  # sandbox, production, simulation
    workshop_pin = db.Column(db.String(20), default="1234")
    device_secret = db.Column(db.String(64), default="garage_terminal_sec_2026")
    quick_services = db.Column(db.String(255), default="Τακτικό Service / Λάδια,Φρένα / Τακάκια,Διαγνωστικός Έλεγχος,Προετοιμασία ΚΤΕΟ")
    last_test_date = db.Column(db.DateTime, nullable=True)
    last_test_status = db.Column(db.String(50), default="UNVERIFIED")
    last_test_message = db.Column(db.String(255), nullable=True)

    @classmethod
    def get_settings(cls):
        import os
        settings = cls.query.first()
        if not settings:
            settings = cls(
                garage_name=os.environ.get('GARAGE_NAME', "Συνεργείο Αυτοκινήτων - Garage Pro"),
                vat_number=os.environ.get('GARAGE_VAT', "094123456"),
                phone=os.environ.get('GARAGE_PHONE', "210 285 1201"),
                address=os.environ.get('GARAGE_ADDRESS', "Αθήνα, Ελλάδα"),
                email=os.environ.get('GARAGE_EMAIL', "info@garage.gr"),
                aade_user_id=os.environ.get('AADE_USER_ID', "autostatus_workshop"),
                aade_subscription_key=os.environ.get('AADE_SUBSCRIPTION_KEY', "demo_sandbox_key_aade_2026"),
                aade_env=os.environ.get('AADE_ENV', "sandbox"),
                workshop_pin=os.environ.get('WORKSHOP_PIN', "1234"),
                device_secret="garage_terminal_sec_2026",
                quick_services="Τακτικό Service / Λάδια,Φρένα / Τακάκια,Διαγνωστικός Έλεγχος,Προετοιμασία ΚΤΕΟ",
                last_test_status="READY",
                last_test_message="Προεπιλεγμένη σύνδεση myDATA Sandbox"
            )
            db.session.add(settings)
            db.session.commit()
        else:
            updated = False
            if not getattr(settings, 'device_secret', None):
                settings.device_secret = "garage_terminal_sec_2026"
                updated = True
            if not getattr(settings, 'phone', None):
                settings.phone = os.environ.get('GARAGE_PHONE', "210 285 1201")
                updated = True
            if not getattr(settings, 'address', None):
                settings.address = os.environ.get('GARAGE_ADDRESS', "Αθήνα, Ελλάδα")
                updated = True
            if not getattr(settings, 'email', None):
                settings.email = os.environ.get('GARAGE_EMAIL', "info@garage.gr")
                updated = True
            if not getattr(settings, 'quick_services', None):
                settings.quick_services = "Τακτικό Service / Λάδια,Φρένα / Τακάκια,Διαγνωστικός Έλεγχος,Προετοιμασία ΚΤΕΟ"
                updated = True
            if updated:
                db.session.commit()
        return settings

    def reset_devices(self):
        import uuid
        self.device_secret = uuid.uuid4().hex[:16]
        db.session.commit()
        return self.device_secret

    def to_dict(self):
        return {
            'garage_name': self.garage_name,
            'vat_number': self.vat_number,
            'phone': self.phone or '210 285 1201',
            'address': self.address or 'Αθήνα, Ελλάδα',
            'email': self.email or 'info@garage.gr',
            'aade_user_id': self.aade_user_id,
            'aade_subscription_key': self.aade_subscription_key,
            'aade_env': self.aade_env,
            'workshop_pin': self.workshop_pin or '1234',
            'device_secret': self.device_secret or 'garage_terminal_sec_2026',
            'quick_services': self.quick_services or "Τακτικό Service / Λάδια,Φρένα / Τακάκια,Διαγνωστικός Έλεγχος,Προετοιμασία ΚΤΕΟ",
            'last_test_date': self.last_test_date.strftime('%d/%m/%Y %H:%M') if self.last_test_date else 'Δεν έχει γίνει έλεγχος',
            'last_test_status': self.last_test_status,
            'last_test_message': self.last_test_message or ''
        }

