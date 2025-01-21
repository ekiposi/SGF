from datetime import datetime, timedelta
import random
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
import os
from models import db, User, Employee, Attendance, Vacation, Schedule
from sqlalchemy import func, or_
from sqlalchemy.sql import text, case
import json
import base64
from io import BytesIO
from PIL import Image
import face_recognition
import schedule as sdl
import time
import subprocess
import mysql.connector
from mysql.connector import Error
import pytz

DEFAULT_SETTINGS = {
    'backup_frequency': 'daily',
    'retention_period': 30
}

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://avnadmin:AVNS_27qYEQ73um8LQWstz1f@mysql-b8de79d-freefire-fcea.g.aivencloud.com:25829/defaultdb'
# app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:Dinu30903%40@localhost/scyan'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(app.static_folder)
BACKUP_DIR = os.path.join(app.root_path, 'instance', 'backups')

if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Initialize SQLAlchemy with app
db.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def get_eastern_time():
    # Always return UTC-5 regardless of server location
    eastern_tz = pytz.timezone('America/New_York')
    return datetime.now(eastern_tz)

def make_aware(dt, timezone='America/New_York'):
    if dt.tzinfo is None:
        tz = pytz.timezone(timezone)
        return tz.localize(dt)
    return dt

# Routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for('admin'))
        return redirect(url_for('profile'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            flash('Connexion réussie.', 'success')
            return redirect(url_for('index'))
            
        flash('Nom d\'utilisateur ou mot de passe invalide.', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin():
    if not current_user.is_admin:
        return redirect(url_for('profile'))
    return render_template('admin.html')

@app.route('/update-admin-credentials', methods=['POST'])
def update_admin_credentials():
    if current_user.is_admin:
        try:
            data = request.get_json()
            new_username = data.get('username')
            new_password = data.get('password')

            if not new_username or not new_password:
                return jsonify({'status': 'error', 'message': 'Le nom d\'utilisateur et le mot de passe sont requis'})
            
            admin_data = User.query.filter_by(username=current_user.username).first()
            if admin_data:
                admin_data.username = new_username
                admin_data.set_password(new_password)
                db.session.commit()
                return jsonify({'status': 'success', 'message': 'Identifiants administrateur mis à jour avec succès'})
            else:
                return jsonify({'status': 'error', 'message': 'Aucun utilisateur trouvé.'})

        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})

def generate_attendance_report(attendances):
    """Generate a report from filtered attendance records"""
    report_data = []
    for attendance in attendances:
        employee = attendance.employee
        report_data.append({
            "employee_name": employee.full_name,
            "department": employee.department,
            "check_in": attendance.check_in.strftime('%I:%M %p'),
            "status": attendance.status,
            "date": attendance.check_in.strftime('%Y-%m-%d')
        })
    return report_data

@app.route('/general', methods=['GET', 'POST'])
@login_required
def general():
    if not current_user.is_admin:
        return redirect(url_for('profile'))
    
    if request.method == 'POST':
        data = request.get_json()
        
        # Handle face recognition toggle
        if 'face-recg' in data:
            with open('config.json') as config_file:
                config_data = json.load(config_file)
            config_data['face-recg'] = data['face-recg']
            
            with open('config.json', 'w') as config_file:
                json.dump(config_data, config_file, indent=4)
            
            return jsonify({"success": True, "face-recg": config_data['face-recg']}), 200
        
        # Handle attendance report generation
        if 'report_type' in data:
            status_filter = data['report_type']
            attendances = Attendance.query.filter_by(status=status_filter).all()
            report_data = generate_attendance_report(attendances)
            return jsonify({"success": True, "report": report_data}), 200

    # Read configuration
    with open('config.json') as config_file:
        config_data = json.load(config_file)
    
    face_recg_enabled = config_data.get('face-recg', False)
    in_time = datetime.strptime(config_data['inTime'], "%H:%M:%S").time()
    attendance_data = []

    # Get all attendances and calculate status
    attendances = Attendance.query.all()
    
    for attendance in attendances:
        employee = attendance.employee
        
        # Calculate total hours if checked out
        if attendance.check_out:
            total_hours = (attendance.check_out - attendance.check_in).total_seconds() / 3600
            total_hours_str = f"{int(total_hours)}:{int((total_hours % 1) * 60):02d}"
        else:
            total_hours_str = None

        # Calculate status if not already set
        if not attendance.status:
            attendance.calculate_status(in_time)
            db.session.commit()
        
        attendance_data.append({
            "employee": employee.full_name,
            "photo": employee.photo,
            "id": employee.pluri_id,
            "department": employee.department,
            "check_in": attendance.check_in.strftime('%I:%M %p'),
            "face_validation": True,
            "check_out": attendance.check_out.strftime('%I:%M %p') if attendance.check_out else None,
            "total_hours": total_hours_str,
            "status": attendance.status
        })
    
    return render_template('general.html',
                         face_recg_enabled=face_recg_enabled,
                         attendance_data=attendance_data)


@app.route('/employees', methods=['GET', 'POST'])
@login_required
def employees():
    if not current_user.is_admin:
        return redirect(url_for('profile'))
        
    if request.method == 'POST':
        # Debug print
        print("Form data:", request.form)
        
        # Generate unique employee ID
        pluri_id = str(random.randint(10000, 99999))
        
        # Split full name into first and last name
        full_name = request.form.get('full_name', '').strip()
        names = full_name.split(' ', 1)
        first_name = names[0]
        last_name = names[1] if len(names) > 1 else ''
        
        # Get role from form
        role = request.form.get('role')
        print("Selected role:", role)  # Debug print

        profile = request.files.get('profile_picture')
        if profile and profile.filename:
            filename = secure_filename(profile.filename)
            extension = os.path.splitext(filename)[1]
            new_filename = f"{pluri_id}{extension}"
            upload_folder = os.path.join(app.config['UPLOAD_FOLDER'],'uploads', 'profiles')
            os.makedirs(upload_folder, exist_ok=True)
            profile.save(os.path.join(upload_folder, new_filename))
            photo_path = f"uploads/profiles/{new_filename}"
        else:
            photo_path = None
        
        # Create new employee
        employee = Employee(
            pluri_id=pluri_id,
            first_name=first_name,
            photo = photo_path,
            last_name=last_name,
            email=request.form.get('email'),
            phone=request.form.get('phone'),
            gender=request.form.get('gender'),
            department=request.form.get('department'),
            position=role,  # Save the role in the position field
            hire_date=datetime.strptime(request.form.get('hire_date'), '%Y-%m-%d').date(),
            dob=datetime.strptime(request.form.get('dob'), '%Y-%m-%d').date()
        )
        
        # Generate QR code
        employee.generate_qr_code()
        
        # Create user account for employee
        user = User(
            username=f"user_{pluri_id}",
            is_admin=False
        )
        user.set_password(pluri_id)  # Initial password is their employee ID
        
        # Link user and employee
        employee.user = user
        
        db.session.add(employee)
        db.session.add(user)
        db.session.commit()
        
        print("Saved employee position:", employee.position)  # Debug print
        
        flash('Employé ajouté avec succès.', 'success')
        return redirect(url_for('employees'))
    
    # GET request - show all employees
    employees_list = Employee.query.all()
    for emp in employees_list:  # Debug print
        print(f"Employee: {emp.full_name}, Position: {emp.position}")
    return render_template('employees.html', employees=employees_list)

@app.route('/employee/<int:employee_id>/update', methods=['GET', 'POST'])
def update_employee(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    
    if request.method == 'POST':
        # Update fields from the form
        employee.first_name = request.form.get('first_name')
        employee.last_name = request.form.get('last_name')
        employee.email = request.form.get('email')
        employee.phone = request.form.get('phone')
        employee.gender = request.form.get('gender')
        employee.department = request.form.get('department')
        employee.position = request.form.get('role')
        employee.hire_date = datetime.strptime(request.form.get('hire_date'), '%Y-%m-%d').date()
        employee.dob = datetime.strptime(request.form.get('dob'), '%Y-%m-%d').date()

        # Handle photo upload
        if 'photo' in request.files:
            photo = request.files['photo']
            if photo.filename != '':
                filename = secure_filename(photo.filename)
                extension = os.path.splitext(filename)[1]
                new_filename = f"{employee.pluri_id}{extension}"
                upload_folder = os.path.join(app.config['UPLOAD_FOLDER'],'uploads', 'profiles')
                os.makedirs(upload_folder, exist_ok=True)
                photo.save(os.path.join(upload_folder, new_filename))
                photo_path = f"uploads/profiles/{new_filename}"

        db.session.commit()
        flash('Employé mis à jour avec succès.', 'success')
        return redirect(url_for('employees'))

    return render_template('update.html', employee=employee)

@app.route('/employee/<int:employee_id>/delete', methods=['GET'])
def delete_employee(employee_id):
    employee = Employee.query.get_or_404(employee_id)

    try:
        # First delete all attendance records for this employee
        Attendance.query.filter_by(employee_id=employee_id).delete()

        # Then delete employee's files
        if employee.photo:
            photo_path = os.path.join(app.config['UPLOAD_FOLDER'], employee.photo)
            if os.path.exists(photo_path):
                os.remove(photo_path)
        qr_path = os.path.join('static', 'uploads', 'qrcodes', f'qr_{employee.pluri_id}')
        if os.path.exists(qr_path):
            os.remove(qr_path)

        # Finally delete the employee
        db.session.delete(employee)
        db.session.commit()
        flash('Employé supprimé avec succès.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la suppression: {str(e)}', 'danger')

    return redirect(url_for('employees'))

def handle_attendance(employee):
    now = get_eastern_time()
    today_start = make_aware(now.replace(hour=0, minute=0, second=0, microsecond=0))
    today_end = make_aware(now.replace(hour=23, minute=59, second=59, microsecond=999999))
    
    # Check for existing attendance today
    attendance = Attendance.query.filter(
        Attendance.employee_id == employee.id,
        Attendance.check_in >= today_start,
        Attendance.check_in <= today_end
    ).first()
    
    if attendance:
        if not attendance.check_out:
            # Check out
            attendance.check_out = now
            attendance.total_hours = (attendance.check_out - make_aware(attendance.check_in)).total_seconds() / 3600
            db.session.commit()
            return {
                'status': 'success',
                'message': f'Départ enregistré avec succès à {now.strftime("%I:%M %p")}',
                'type': 'check_out'
            }
        else:
            return {
                'status': 'error',
                'message': 'Vous avez déjà enregistré votre départ aujourd\'hui.'
            }
    else:
        # Check in
        day_of_week = now.weekday()
        schedule = Schedule.query.filter_by(employee_id=employee.id, day_of_week=day_of_week).first()

        if schedule:
            attendance = Attendance(
                employee_id=employee.id,
                check_in=now,
            )
            attendance.status = attendance.calculate_status(schedule.start_time)
            db.session.add(attendance)
            db.session.commit()
        else:
            print('NO schedule')
            return {
            'status': 'error',
            'message': f'No schedule found for the employee.',
        }
        return {
            'status': 'success',
            'message': f'Arrivée enregistrée avec succès à {now.strftime("%I:%M %p")}',
            'type': 'check_in'
        }

@app.route('/scan', methods=['GET', 'POST'])
@login_required
def scan():
    if not current_user.is_admin:
        return redirect(url_for('profile'))
        
    if request.method == 'POST':
        try:
            with open('config.json') as config_file:
                config_data = json.load(config_file)
            
            data = request.json
            pluri_id = data.get('pluriId')
            
            employee = Employee.query.filter_by(pluri_id=pluri_id).first()
            if not employee:
                return jsonify({'status': 'error', 'message': 'Code QR invalide'}), 400
            
            if not config_data['face-recg']:
                attendance_response = handle_attendance(employee)
                
                return jsonify({
                    **attendance_response,
                    'faceEnabled': config_data['face-recg'],
                    'empId': employee.pluri_id
                }), 200
            else:
                return jsonify({
                    'status':'success',
                    'faceEnabled': config_data['face-recg'],
                    'empId': employee.pluri_id
                }), 200
                
        except Exception as e:
            print(f"Erreur dans le scan: {str(e)}")
            return jsonify({'status': 'error', 'message': 'Erreur serveur'}), 500
            
    return render_template('scan.html')

@app.route('/credits')
def credits():
    return render_template('credits.html')

@app.route('/facial-recognition', methods=['POST'])
def facial_recognition():
    if not os.path.exists('static/uploads/face_snapshots'):
        os.makedirs('static/uploads/face_snapshots', exist_ok=True)
    data = request.json
    image_data = data.get('image_data')
    emp_id = data.get('emp_id')

    if not image_data or not emp_id:
        return jsonify({'status': 'error', 'message': 'Données invalides fournies'}), 400

    image = Image.open(BytesIO(base64.b64decode(image_data.split(',')[1])))
    image.save(f'static/uploads/face_snapshots/{emp_id}_face.jpg')
    profile_pic = None
    for i in os.listdir("static/uploads/profiles/"):
        if emp_id in i:
            profile_pic = i
            break
    if not profile_pic:
        return jsonify({'status': 'error', 'message': 'Veuillez mettre à jour votre photo de profil'}), 404
    
    known_image = face_recognition.load_image_file("static/uploads/profiles/" + profile_pic)
    unknown_image = face_recognition.load_image_file(f'static/uploads/face_snapshots/{emp_id}_face.jpg')
    biden_encoding = face_recognition.face_encodings(known_image)[0]
    unknown_encoding = face_recognition.face_encodings(unknown_image)[0]
    if len(biden_encoding) == 0 or len(unknown_encoding) == 0:
        return jsonify({'status': 'error', 'message': 'Aucun visage détecté dans l\'une ou les deux images'}), 404
    results = face_recognition.compare_faces([biden_encoding], unknown_encoding, tolerance=0.4)
    if os.path.exists(f'static/uploads/face_snapshots/{emp_id}_face.jpg'):
        os.remove(f'static/uploads/face_snapshots/{emp_id}_face.jpg')
    if results[0]:
        employee = Employee.query.filter_by(pluri_id=emp_id).first()
        if not employee:
                return jsonify({'status': 'error', 'message': 'Code QR invalide'}), 400
        attendance_response = handle_attendance(employee)
        
        return jsonify({
            **attendance_response,
        })
    else:
        return jsonify({'status': 'error', 'message': 'Les visages ne correspondent pas. Veuillez vous assurer que le visage est clairement visible.'})

@app.route('/today_attendance')
@login_required
def today_attendance():
    today = get_eastern_time()
    today_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    # Get all attendance records for today
    attendance_records = Attendance.query.filter(
        Attendance.check_in >= today_start,
        Attendance.check_in <= today_end
    ).order_by(Attendance.check_in.desc()).all()
    
    # Format the records for JSON response
    records = []
    for attendance in attendance_records:
        # Calculate total hours if check_out exists
        total_hours = None
        if attendance.check_out:
            duration = attendance.check_out - attendance.check_in
            total_hours = duration.total_seconds() / 3600  # Convert to hours
        
        record = {
            'pluri_id': attendance.employee.pluri_id,
            'department': attendance.employee.department,
            'position': attendance.employee.position,
            'employee_name': attendance.employee.full_name,
            'check_in': attendance.check_in.strftime('%I:%M %p'),
            'check_out': attendance.check_out.strftime('%I:%M %p') if attendance.check_out else None,
            'total_hours': round(total_hours, 2) if total_hours is not None else None
        }
        records.append(record)
    
    return jsonify(records)

@app.route('/report')
@login_required
def report():
    if not current_user.is_admin:
        return redirect(url_for('profile'))
    return render_template('report.html')

@app.route('/api/report_data')
@login_required
def report_data():
    if not current_user.is_admin:
        return jsonify({'error': 'Non autorisé'}), 403

    employee_name = request.args.get('employee_name', '')
    pluri_id = request.args.get('pluri_id', '')
    department = request.args.get('department', '')
    function = request.args.get('function', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    query = db.session.query(
        Employee.pluri_id,
        Employee.first_name,
        Employee.last_name,
        Employee.department,
        Employee.position,
        func.count(case([(Attendance.status == 'present', 1)])).label('days_present'),
        func.count(case([(Attendance.status == 'late', 1)])).label('days_late'),
        func.count(case([(Attendance.status == 'absent', 1)])).label('days_absent'),
        func.count(case([(Attendance.status == 'vacation', 1)])).label('days_vacation'),
        func.sum(
            func.coalesce(
                func.timestampdiff(
                    text('SECOND'),
                    Attendance.check_in,
                    Attendance.check_out
                ),
                0
            )
        ).label('total_worked_seconds')
    ).join(
        Attendance,
        Employee.id == Attendance.employee_id
    ).group_by(
        Employee.id,
        Employee.pluri_id,
        Employee.first_name,
        Employee.last_name,
        Employee.department,
        Employee.position
    )

    # Apply filters
    if employee_name:
        name_parts = employee_name.strip().split()
        if len(name_parts) > 1:
            query = query.filter(
                db.or_(
                    db.and_(
                        Employee.first_name.ilike(f'%{name_parts[0]}%'),
                        Employee.last_name.ilike(f'%{name_parts[-1]}%')
                    ),
                    db.and_(
                        Employee.first_name.ilike(f'%{name_parts[-1]}%'),
                        Employee.last_name.ilike(f'%{name_parts[0]}%')
                    )
                )
            )
        else:
            query = query.filter(
                db.or_(
                    Employee.first_name.ilike(f'%{employee_name}%'),
                    Employee.last_name.ilike(f'%{employee_name}%')
                )
            )

    if pluri_id:
        query = query.filter(Employee.pluri_id == pluri_id)
    if department:
        query = query.filter(Employee.department == department)
    if function:
        query = query.filter(Employee.position == function)
    if date_from:
        query = query.filter(Attendance.check_in >= datetime.strptime(date_from, '%Y-%m-%d'))
    if date_to:
        query = query.filter(Attendance.check_in <= datetime.strptime(date_to, '%Y-%m-%d'))

    records = []
    for record in query.all():
        total_hours = round(float(record.total_worked_seconds or 0) / 3600, 2)
        records.append({
            'pluri_id': record.pluri_id,
            'full_name': f'{record.first_name} {record.last_name}',
            'department': record.department,
            'function': record.position,
            'days_present': record.days_present,
            'days_late': record.days_late,
            'days_absent': record.days_absent,
            'days_vacation': record.days_vacation,
            'total_hours': total_hours
        })
    
    return jsonify({'data': records})

@app.route('/api/search_employees')
@login_required
def search_employees():
    term = request.args.get('term', '')
    if len(term) < 2:
        return jsonify([])

    employees = Employee.query.filter(
        db.or_(
            Employee.first_name.ilike(f'%{term}%'),
            Employee.last_name.ilike(f'%{term}%'),
            Employee.pluri_id.ilike(f'%{term}%')
        )
    ).limit(10).all()

    return jsonify([{
        'id': emp.id,
        'pluri_id': emp.pluri_id,
        'full_name': emp.full_name
    } for emp in employees])


@app.route('/profile')
@login_required
def profile():
    today = get_eastern_time()
    today_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    # Get today's attendance
    today_attendance = Attendance.query.filter(
        Attendance.employee_id == current_user.employee.id,
        Attendance.check_in >= today_start,
        Attendance.check_in <= today_end
    ).first()
    
    # Get past attendance records (excluding today)
    past_attendance = Attendance.query.filter(
        Attendance.employee_id == current_user.employee.id,
        Attendance.check_in < today_start
    ).order_by(Attendance.check_in.desc()).all()
    
    return render_template('profile.html', 
                         today=today,
                         today_attendance=today_attendance,
                         past_attendance=past_attendance)

@app.route('/filter_attendance', methods=['GET'])
def filter_attendance():
    filter_type = request.args.get('filter')
    employee_id = 1

    today = get_eastern_time()
    start_date = None

    if filter_type == 'week':
        start_date = today - timedelta(days=7)
    elif filter_type == 'month':
        start_date = today - timedelta(days=30)

    query = Attendance.query.filter(Attendance.employee_id == employee_id)
    if start_date:
        query = query.filter(Attendance.check_in >= start_date)

    records = query.order_by(Attendance.check_in.desc()).all()

    attendance_data = []
    for record in records:
        attendance_data.append({
            'date': record.check_in.strftime('%Y-%m-%d'),
            'check_in': record.check_in.strftime('%I:%M %p'),
            'check_out': record.check_out.strftime('%I:%M %p') if record.check_out else '-',
            'total_hours': f"{record.total_hours:.2f}" if record.total_hours else '-',
        })

    return jsonify(attendance_data)


@app.route('/dashboard')
@login_required
def dashboard():
    with open('config.json') as config_file:
        config_data = json.load(config_file)

    # Convert inTime and outTime from config to time objects
    in_time = datetime.strptime(config_data['inTime'], "%H:%M:%S").time()
    out_time = datetime.strptime(config_data['outTime'], "%H:%M:%S").time()

    total_employees = Employee.query.count()

    present_employees = Attendance.query.filter(
        Attendance.check_in >= get_eastern_time().replace(hour=0, minute=0, second=0)
    ).count()

    if config_data['lateComers']:
        latecomers = Attendance.query.filter(
            Attendance.check_in >= get_eastern_time().replace(hour=0, minute=0, second=0),
            func.time(Attendance.check_in) > in_time
        ).count()
    else:
        latecomers = 0

    # Logic for employees on leave
    employees_on_leave = Employee.query.filter(
        ~Employee.id.in_(
            db.session.query(Attendance.employee_id).filter(
                or_(
                    func.time(Attendance.check_in).between(in_time, out_time),
                    func.time(Attendance.check_out).between(in_time, out_time)
                ),
                Attendance.check_in >= get_eastern_time().replace(hour=0, minute=0, second=0)
            )
        )
    ).count()

    male_employees = Employee.query.filter_by(gender='male').count()
    female_employees = Employee.query.filter_by(gender='female').count()

    # Weekly activity data
    activity_labels = []
    activity_data = []
    for i in range(7):
        day = get_eastern_time().date() - timedelta(days=i)
        activity_labels.append(day.strftime('%A'))
        activity_data.append(
            Attendance.query.filter(
                Attendance.check_in >= day,
                Attendance.check_in < day + timedelta(days=1)
            ).count()
        )
    
    today_start = get_eastern_time().replace(hour=0, minute=0, second=0)
    today_attendance = Attendance.query.filter(Attendance.check_in >= today_start).all()

    return render_template(
        'dashboard.html',
        total_employees=total_employees,
        present_employees=present_employees,
        latecomers=latecomers,
        employees_on_leave=employees_on_leave,
        male_employees=male_employees,
        female_employees=female_employees,
        activity_labels=activity_labels[::-1],
        activity_data=activity_data[::-1],
        today_attendance=today_attendance
    )

@app.route('/activity-data', methods=['GET'])
def activity_data():
    # Get the period parameter (day, week, month, year)
    period = request.args.get('period', 'week').lower()
    
    activity_labels = []
    activity_data = []
    
    if period == 'day':
        # Group data by hour for the current day
        current_date = get_eastern_time().date()
        for hour in range(24):
            start_time = datetime.combine(current_date, datetime.min.time()) + timedelta(hours=hour)
            end_time = start_time + timedelta(hours=1)
            activity_labels.append(f"{hour}:00")
            activity_data.append(
                Attendance.query.filter(
                    Attendance.check_in >= start_time,
                    Attendance.check_in < end_time
                ).count()
            )
    elif period == 'week':
        # Group data by last 7 days
        for i in range(7):
            day = get_eastern_time().date() - timedelta(days=i)
            activity_labels.insert(0, day.strftime('%A'))  # Add to the start for correct order
            activity_data.insert(0, 
                Attendance.query.filter(
                    Attendance.check_in >= day,
                    Attendance.check_in < day + timedelta(days=1)
                ).count()
            )
    elif period == 'month':
        # Group data by days in the current month
        current_date = get_eastern_time()
        start_of_month = datetime(current_date.year, current_date.month, 1)
        num_days = (datetime(current_date.year, current_date.month + 1, 1) - start_of_month).days
        for day in range(num_days):
            start_time = start_of_month + timedelta(days=day)
            end_time = start_time + timedelta(days=1)
            activity_labels.append(start_time.strftime('%d %b'))
            activity_data.append(
                Attendance.query.filter(
                    Attendance.check_in >= start_time,
                    Attendance.check_in < end_time
                ).count()
            )
    elif period == 'year':
        # Group data by months in the current year
        current_year = get_eastern_time().year
        for month in range(1, 13):
            start_time = datetime(current_year, month, 1)
            if month == 12:
                end_time = datetime(current_year + 1, 1, 1)
            else:
                end_time = datetime(current_year, month + 1, 1)
            activity_labels.append(start_time.strftime('%b'))
            activity_data.append(
                Attendance.query.filter(
                    Attendance.check_in >= start_time,
                    Attendance.check_in < end_time
                ).count()
            )
    
    return jsonify({
        "labels": activity_labels,
        "data": activity_data
    })

def create_settings_table():
    try:
        conn = mysql.connector.connect(
            host='localhost',
            user='root',
            password='Dinu30903@',
            database='scyan',
            port=3306
        )
        # conn = mysql.connector.connect(
        #     host='mysql-2a126491-exodus.g.aivencloud.com',
        #     user='avnadmin',
        #     password='AVNS_YA14kHqPLGBGgSL3r91',
        #     database='defaultdb',
        #     port=18235
        # )
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                `key` VARCHAR(50) PRIMARY KEY,
                value VARCHAR(255) NOT NULL
            )
        ''')
        for key, value in DEFAULT_SETTINGS.items():
            cursor.execute('''
                INSERT IGNORE INTO settings (`key`, value) 
                VALUES (%s, %s)
            ''', (key, str(value)))
        
        conn.commit()
        cursor.close()
        conn.close()
    except Error as e:
        print(f"Error creating settings table: {e}")

@app.route('/backup')
@login_required
def backup():
    if not current_user.is_admin:
        return redirect(url_for('profile'))
    
    backups = []
    for filename in os.listdir(BACKUP_DIR):
        if filename.startswith('database_backup_'):
            file_path = os.path.join(BACKUP_DIR, filename)
            file_stats = os.stat(file_path)
            timestamp_str = filename.replace('database_backup_', '').replace('.sql', '')
            file_date = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
            backups.append({
                'id': filename,
                'date': file_date,
                'size': f"{file_stats.st_size / (1024*1024):.2f} MB"
            })
    
    backups.sort(key=lambda x: x['date'], reverse=True)
    
    settings = DEFAULT_SETTINGS.copy() 
    try:
        conn = mysql.connector.connect(
            host='localhost',
            user='root',
            password='Dinu30903@',
            database='scyan',
            port=3306
        )
        # conn = mysql.connector.connect(
        #     host='mysql-2a126491-exodus.g.aivencloud.com',
        #     user='avnadmin',
        #     password='AVNS_YA14kHqPLGBGgSL3r91',
        #     database='defaultdb',
        #     port=18235
        # )
        cursor = conn.cursor()
        create_settings_table()
        
        cursor.execute('SELECT `key`, value FROM settings')
        db_settings = cursor.fetchall()
        for key, value in db_settings:
            settings[key] = value
        
        cursor.close()
        conn.close()
    except Error as e:
        print(f"Error fetching settings: {e}")
    
    return render_template('backup.html', backups=backups, settings=settings)

@app.route('/backup/create', methods=['POST'])
@login_required
def create_backup():
    if not current_user.is_admin:
        return redirect(url_for('profile'))
    
    backup_path = create_backup_file()
    if backup_path:
        flash('Sauvegarde créée avec succès', 'success')
    else:
        flash('Erreur lors de la création de la sauvegarde', 'error')
    
    return redirect(url_for('backup'))

@app.route('/backup/settings', methods=['POST'])
@login_required
def update_backup_settings():
    if not current_user.is_admin:
        return redirect(url_for('profile'))
    
    frequency = request.form.get('backup_frequency', DEFAULT_SETTINGS['backup_frequency'])
    retention_period = request.form.get('retention_period', str(DEFAULT_SETTINGS['retention_period']))
    
    try:
        conn = mysql.connector.connect(
            host='localhost',
            user='root',
            password='Dinu30903@',
            database='scyan',
            port=3306
        )
        # conn = mysql.connector.connect(
        #     host='mysql-2a126491-exodus.g.aivencloud.com',
        #     user='avnadmin',
        #     password='AVNS_YA14kHqPLGBGgSL3r91',
        #     database='defaultdb',
        #     port=18235
        # )
        cursor = conn.cursor()
        create_settings_table()
        cursor.execute('''
            INSERT INTO settings (`key`, value) 
            VALUES (%s, %s) 
            ON DUPLICATE KEY UPDATE value = VALUES(value)
        ''', ('backup_frequency', frequency))
        
        cursor.execute('''
            INSERT INTO settings (`key`, value) 
            VALUES (%s, %s) 
            ON DUPLICATE KEY UPDATE value = VALUES(value)
        ''', ('retention_period', retention_period))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        schedule_backup(frequency)
        cleanup_old_backups()
        
        flash('Paramètres de sauvegarde mis à jour', 'success')
    except Error as e:
        flash(f'Erreur lors de la mise à jour des paramètres: {str(e)}', 'error')
    
    return redirect(url_for('backup'))

@app.route('/backup/delete/<backup_id>', methods=['POST'])
@login_required
def delete_backup(backup_id):
    if not current_user.is_admin:
        return redirect(url_for('profile'))
    
    try:
        backup_path = os.path.join(BACKUP_DIR, backup_id)
        if os.path.exists(backup_path):
            os.remove(backup_path)
            flash('Sauvegarde supprimée', 'success')
        else:
            flash('Sauvegarde non trouvée', 'error')
    except Exception as e:
        flash(f'Erreur lors de la suppression: {str(e)}', 'error')
    
    return redirect(url_for('backup'))

def create_backup_file():
    timestamp = get_eastern_time().strftime('%Y%m%d_%H%M%S')
    backup_filename = f'database_backup_{timestamp}.sql'
    backup_path = os.path.join(BACKUP_DIR, backup_filename)
    print(backup_path)
    try:
        cmd = [
            'mysqldump',
            f'--host=localhost',
            f'--user=root',
            f'--password=Dinu30903@',
            'scyan'
        ]
        # cmd = [
        #     'mysqldump',
        #     f'--host=mysql-2a126491-exodus.g.aivencloud.com',
        #     f'--user=avnadmin',
        #     f'--password=AVNS_YA14kHqPLGBGgSL3r91',
        #     'defaultdb'
        # ]
        
        with open(backup_path, 'w') as f:
            subprocess.run(cmd, stdout=f, check=True)
        
        return backup_path
    except Exception as e:
        print(f'Erreur lors de la création de la sauvegarde: {str(e)}')
        return None

def schedule_backup(frequency):
    if frequency == 'daily':
        sdl.every().day.at("00:00").do(create_backup_file)
    elif frequency == 'weekly':
        sdl.every().sunday.at("00:00").do(create_backup_file)
    elif frequency == 'monthly':
        sdl.every(1).months.at("00:00").do(create_backup_file)

def cleanup_old_backups():
    try:
        conn = mysql.connector.connect(
            host='mysql-2a126491-exodus.g.aivencloud.com',
            user='avnadmin',
            password='AVNS_YA14kHqPLGBGgSL3r91',
            database='defaultdb',
            port=18235
        )
        cursor = conn.cursor()
        
        create_settings_table()
        
        cursor.execute('SELECT value FROM settings WHERE `key` = "retention_period"')
        result = cursor.fetchone()
        retention_period = int(result[0]) if result else DEFAULT_SETTINGS['retention_period']
        
        cursor.close()
        conn.close()
        
        for filename in os.listdir(BACKUP_DIR):
            if filename.startswith('database_backup_'):
                file_path = os.path.join(BACKUP_DIR, filename)
                timestamp_str = filename.replace('database_backup_', '').replace('.sql', '')
                file_date = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                if (get_eastern_time() - file_date).days > retention_period:
                    os.remove(file_path)
    except Error as e:
        print(f'Erreur lors de la suppression des anciennes sauvegardes: {str(e)}')

@app.route('/edit_schedules', methods=['GET', 'POST'])
@login_required
def edit_schedules():
    if not current_user.is_admin:
        return redirect(url_for('profile'))

    if request.method == 'POST':
        data = request.get_json()
        action = data.get('action')

        if action == 'search_employee':
            search_term = data.get('term', '').strip().lower()
            name_parts = search_term.split()

            if len(name_parts) == 1:
                employees = Employee.query.filter(
                    or_(
                        func.lower(Employee.first_name).contains(search_term),
                        func.lower(Employee.last_name).contains(search_term),
                        Employee.pluri_id.contains(search_term)
                    )
                ).limit(10).all()
            else:
                first_name = name_parts[0]
                last_name = name_parts[-1]
                employees = Employee.query.filter(
                    or_(
                        func.lower(Employee.first_name + ' ' + Employee.last_name).contains(search_term),
                        func.and_(
                            func.lower(Employee.first_name).contains(first_name),
                            func.lower(Employee.last_name).contains(last_name)
                        ),
                        Employee.pluri_id.contains(search_term)
                    )
                ).limit(10).all()

            return jsonify([{
                'id': emp.id,
                'name': emp.full_name,
                'pluri_id': emp.pluri_id
            } for emp in employees])

        elif action == 'save_schedule':
            employee_id = data.get('employee_id')
            schedules = data.get('schedules', [])
            repeat_until = data.get('repeat_until')

            try:
                Schedule.query.filter_by(employee_id=employee_id).delete()

                for schedule in schedules:
                    new_schedule = Schedule(
                        employee_id=employee_id,
                        day_of_week=schedule['day'],
                        start_time=datetime.strptime(schedule['start'], '%H:%M').time(),
                        end_time=datetime.strptime(schedule['end'], '%H:%M').time(),
                        repeat_until=datetime.strptime(repeat_until, '%Y-%m-%d').date() if repeat_until else None
                    )
                    db.session.add(new_schedule)

                db.session.commit()
                return jsonify({'status': 'success'})
            except Exception as e:
                db.session.rollback()
                return jsonify({'status': 'error', 'message': str(e)})

        elif action == 'save_vacation':
            employee_id = data.get('employee_id')
            start_date = data.get('start_date')
            end_date = data.get('end_date')

            try:
                new_vacation = Vacation(
                    employee_id=employee_id,
                    start_date=datetime.strptime(start_date, '%Y-%m-%d').date(),
                    end_date=datetime.strptime(end_date, '%Y-%m-%d').date()
                )
                db.session.add(new_vacation)
                db.session.commit()
                return jsonify({'status': 'success'})
            except Exception as e:
                db.session.rollback()
                return jsonify({'status': 'error', 'message': str(e)})

    return render_template('schedules.html')

def check_inactive_employees():
    today = get_eastern_time().date()
    two_days_ago = today - timedelta(days=2)
    
    inactive_employees = Employee.query.filter(
        ~Employee.id.in_(
            db.session.query(Schedule.employee_id).filter(
                Schedule.day_of_week.in_([today.weekday(), (today - timedelta(days=1)).weekday()]),
                or_(Schedule.repeat_until.is_(None), Schedule.repeat_until >= today)
            )
        )
    ).all()
    
    return inactive_employees

@app.route('/schedules/employee/<int:employee_id>', methods=['GET'])
@login_required
def get_employee_schedule(employee_id):
    try:
        current_date = get_eastern_time().date()
        schedules = Schedule.query.filter(
            Schedule.employee_id == employee_id,
            or_(
                Schedule.repeat_until.is_(None),
                Schedule.repeat_until >= current_date
            )
        ).all()

        schedule_data = []
        for schedule in schedules:
            schedule_data.append({
                'day_of_week': schedule.day_of_week,
                'start_time': schedule.start_time.strftime('%H:%M'),
                'end_time': schedule.end_time.strftime('%H:%M')
            })

        vacations = Vacation.query.filter(
            Vacation.employee_id == employee_id,
            Vacation.end_date >= current_date
        ).order_by(Vacation.start_date).all()

        vacation_data = []
        for vacation in vacations:
            vacation_data.append({
                'start_date': vacation.start_date.strftime('%Y-%m-%d'),
                'end_date': vacation.end_date.strftime('%Y-%m-%d')
            })

        repeat_until = None
        repeating_schedule = Schedule.query.filter(
            Schedule.employee_id == employee_id,
            Schedule.repeat_until.isnot(None)
        ).order_by(Schedule.repeat_until.desc()).first()

        if repeating_schedule and repeating_schedule.repeat_until >= current_date:
            repeat_until = repeating_schedule.repeat_until.strftime('%Y-%m-%d')

        return jsonify({
            'status': 'success',
            'schedules': schedule_data,
            'vacations': vacation_data,
            'repeat_until': repeat_until
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

def find_employees_without_schedules():
    
    today = get_eastern_time().date()
    today_day_of_week = today.weekday()
    next_day_of_week = (today_day_of_week + 1) % 7
    scheduled_employee_ids = Schedule.query.with_entities(Schedule.employee_id).filter(
        (Schedule.day_of_week == next_day_of_week) | (Schedule.day_of_week == next_day_of_week + 1)
    ).distinct()

    employees_without_schedules = Employee.query.filter(
        ~Employee.id.in_(scheduled_employee_ids)
    ).all()
    return employees_without_schedules

@app.route('/schedules')
@login_required
def schedules():
    results = db.session.query(
        Employee,
        Schedule,
        Vacation
    ).join(
        Schedule, Employee.id == Schedule.employee_id
    ).outerjoin(
        Vacation, Employee.id == Vacation.employee_id
    ).all()

    employee_schedules = {}
    for employee, schedule, vacation in results:
        if employee.id not in employee_schedules:
            employee_schedules[employee.id] = {
                'name': f'{employee.first_name} {employee.last_name}',
                'days': [],
                'start_time': schedule.start_time,
                'end_time': schedule.end_time,
                'repeat_until': schedule.repeat_until,
                'vacation_start': vacation.start_date if vacation else None,
                'vacation_end': vacation.end_date if vacation else None
            }
        
        day_names = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
        if schedule.day_of_week >= 0 and schedule.day_of_week <= 6:
            employee_schedules[employee.id]['days'].append(day_names[schedule.day_of_week])

    formatted_schedules = list(employee_schedules.values())
    formatted_schedules.sort(key=lambda x: x['name'])

    return render_template(
        'general_schedule.html', 
        schedules=formatted_schedules,
        current_date=get_eastern_time().strftime('%d/%m/%Y')
    )

@app.route('/notifications')
def notifications():
    return render_template('notifications.html', employees_without_schedules=find_employees_without_schedules())

def run_backup_scheduler():
    while True:
        sdl.run_pending()
        time.sleep(1)

if __name__ == '__main__':
    import threading
    threading.Thread(target=run_backup_scheduler).start()
    app.run(host='0.0.0.0', port=8000, debug=True)
