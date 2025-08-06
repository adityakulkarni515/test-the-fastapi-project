# main.py
# To run this app:
# 1. Install necessary packages:
#    pip install fastapi uvicorn sqlalchemy psycopg2-binary passlib[bcrypt] python-jose[cryptography] python-multipart
#
# 2. Set up your database URL as an environment variable. For Render.com, this is done in the environment settings.
#    Example for local development (in your terminal):
#    export DATABASE_URL="postgresql://user:password@host:port/database_name"
#
# 3. Run the application:
#    uvicorn main:app --reload

import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, Enum, Boolean, TIMESTAMP, ForeignKey, DECIMAL, Date, text
from sqlalchemy.ext.declardeclarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from sqlalchemy.exc import IntegrityError

# --- Configuration ---
# Load database URL from environment variables. Render will provide this.
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/schooldb")

# JWT Settings
SECRET_KEY = "a_very_secret_key_for_jwt" # In production, use a more secure key, possibly from env vars
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# --- Database Setup (SQLAlchemy) ---
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Password Hashing ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# --- SQLAlchemy Models (Mirrors your DB schema) ---

class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    role = Column(Enum('Admin', 'Teaching Staff', 'Non-Teaching Staff', name='user_roles'), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))

class Student(Base):
    __tablename__ = "students"
    student_id = Column(Integer, primary_key=True, index=True)
    admission_number = Column(String, unique=True, index=True, nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    admission_date = Column(Date, nullable=False)
    status = Column(Enum('Active', 'Inactive', 'Graduated', 'Withdrawn', name='student_status'), default='Active')
    created_at = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))

class AcademicYear(Base):
    __tablename__ = "academic_years"
    year_id = Column(Integer, primary_key=True, index=True)
    year_name = Column(String, unique=True, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    is_current = Column(Boolean, default=False)

class StudentAnnualFee(Base):
    __tablename__ = "student_annual_fees"
    annual_fee_id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.student_id"))
    year_id = Column(Integer, ForeignKey("academic_years.year_id"))
    total_annual_fees = Column(DECIMAL(10, 2), nullable=False)
    notes = Column(String)
    student = relationship("Student")
    academic_year = relationship("AcademicYear")

class Transaction(Base):
    __tablename__ = "transactions"
    transaction_id = Column(Integer, primary_key=True, index=True)
    transaction_type = Column(Enum('Fee Payment', 'Expense', 'Income', 'Fee Carry Forward', name='transaction_types'), nullable=False)
    amount = Column(DECIMAL(10, 2), nullable=False)
    transaction_date = Column(Date, nullable=False)
    description = Column(String)
    student_id = Column(Integer, ForeignKey("students.student_id"), nullable=True)
    category = Column(String, nullable=True)
    payment_method = Column(Enum('Cash', 'Bank Transfer', 'Online', 'Cheque', name='payment_methods'), nullable=True)
    reference_details = Column(String, nullable=True)
    recorded_by_user_id = Column(Integer, ForeignKey("users.user_id"))
    year_id = Column(Integer, ForeignKey("academic_years.year_id"))
    
    student = relationship("Student")
    recorded_by = relationship("User")
    academic_year = relationship("AcademicYear")

# This command creates the tables in your database based on the models above.
# You only need to run this once. You can comment it out after the first run.
# Base.metadata.create_all(bind=engine)


# --- Pydantic Schemas (for API input/output validation) ---

class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str
    role: str # 'Admin', 'Teaching Staff', etc.

class UserOut(BaseModel):
    user_id: int
    username: str
    full_name: str
    role: str
    class Config:
        orm_mode = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

class FeePaymentCreate(BaseModel):
    student_id: int
    amount: float
    transaction_date: date
    payment_method: str # 'Cash', 'Bank Transfer', etc.
    reference_details: Optional[str] = None
    description: Optional[str] = None
    year_id: int # The academic year this payment is for

class StudentDetails(BaseModel):
    student_id: int
    admission_number: str
    full_name: str
    status: str
    admission_date: date
    class Config:
        orm_mode = True

class StudentFeeSummary(BaseModel):
    student_details: StudentDetails
    academic_year: str
    total_fees_due: float
    total_amount_paid: float
    pending_fees: float

class TransactionOut(BaseModel):
    transaction_id: int
    transaction_type: str
    amount: float
    transaction_date: date
    description: Optional[str]
    category: Optional[str]
    student_id: Optional[int]
    recorded_by: str # full_name of the user
    class Config:
        orm_mode = True
        
# --- Dependency to get DB session ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Authentication Functions ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception
    user = db.query(User).filter(User.username == token_data.username).first()
    if user is None:
        raise credentials_exception
    return user

# --- FastAPI App Initialization ---
app = FastAPI(title="School Finance API", description="API for managing school financial transactions.")

# --- API Endpoints ---

@app.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    """
    Endpoint for new user registration (signup).
    Only an existing Admin should ideally be able to do this,
    but for initial setup, it's open.
    """
    hashed_password = get_password_hash(user.password)
    db_user = User(
        username=user.username,
        password_hash=hashed_password,
        full_name=user.full_name,
        role=user.role
    )
    try:
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Username already registered")
    return db_user

@app.post("/login", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """
    User login endpoint. Takes username and password, returns a JWT access token.
    """
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/transactions/fee-payment", status_code=status.HTTP_201_CREATED)
def record_fee_payment(
    payment: FeePaymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Allows any logged-in staff member to record a fee payment for a student.
    """
    # Check if student and academic year exist
    student = db.query(Student).filter(Student.student_id == payment.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    year = db.query(AcademicYear).filter(AcademicYear.year_id == payment.year_id).first()
    if not year:
        raise HTTPException(status_code=404, detail="Academic year not found")

    new_transaction = Transaction(
        transaction_type='Fee Payment',
        amount=payment.amount,
        transaction_date=payment.transaction_date,
        description=payment.description,
        student_id=payment.student_id,
        payment_method=payment.payment_method,
        reference_details=payment.reference_details,
        recorded_by_user_id=current_user.user_id,
        year_id=payment.year_id
    )
    db.add(new_transaction)
    db.commit()
    return {"message": "Fee payment recorded successfully"}

@app.get("/students/{student_id}/details", response_model=StudentDetails)
def get_student_details(student_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Get the basic details of a specific student.
    """
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    return StudentDetails(
        student_id=student.student_id,
        admission_number=student.admission_number,
        full_name=f"{student.first_name} {student.last_name}",
        status=student.status,
        admission_date=student.admission_date
    )

@app.get("/students/{student_id}/fee-summary/{year_id}", response_model=StudentFeeSummary)
def get_student_fee_summary(student_id: int, year_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Provides a financial summary for a student for a specific academic year,
    including total fees, amount paid, and pending balance.
    """
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    annual_fee_record = db.query(StudentAnnualFee).filter(
        StudentAnnualFee.student_id == student_id,
        StudentAnnualFee.year_id == year_id
    ).first()

    if not annual_fee_record:
        raise HTTPException(status_code=404, detail=f"No fee record found for student for the specified academic year.")

    total_fees_due = float(annual_fee_record.total_annual_fees)
    
    # Calculate amount paid
    payments = db.query(Transaction).filter(
        Transaction.student_id == student_id,
        Transaction.year_id == year_id,
        Transaction.transaction_type == 'Fee Payment'
    ).all()
    total_amount_paid = sum(float(p.amount) for p in payments)

    # Calculate carry forward amount (dues from previous years added to this year)
    carry_forwards = db.query(Transaction).filter(
        Transaction.student_id == student_id,
        Transaction.year_id == year_id,
        Transaction.transaction_type == 'Fee Carry Forward'
    ).all()
    total_carry_forward = sum(float(cf.amount) for cf in carry_forwards)

    # Total due is the annual fee plus any carried forward balance
    total_fees_due += total_carry_forward
    pending_fees = total_fees_due - total_amount_paid

    student_details = StudentDetails(
        student_id=student.student_id,
        admission_number=student.admission_number,
        full_name=f"{student.first_name} {student.last_name}",
        status=student.status,
        admission_date=student.admission_date
    )

    return StudentFeeSummary(
        student_details=student_details,
        academic_year=annual_fee_record.academic_year.year_name,
        total_fees_due=round(total_fees_due, 2),
        total_amount_paid=round(total_amount_paid, 2),
        pending_fees=round(pending_fees, 2)
    )

@app.get("/transactions/history", response_model=List[TransactionOut])
def get_transaction_history(
    start_date: date, 
    end_date: date, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    """
    Retrieves a list of all transactions (fees, expenses, income)
    within a given date range.
    """
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="Start date cannot be after end date.")

    transactions = db.query(Transaction).join(User).filter(
        Transaction.transaction_date >= start_date,
        Transaction.transaction_date <= end_date
    ).order_by(Transaction.transaction_date.desc()).all()
    
    # Format the output to include the user's full name
    result = []
    for t in transactions:
        result.append(TransactionOut(
            transaction_id=t.transaction_id,
            transaction_type=t.transaction_type,
            amount=float(t.amount),
            transaction_date=t.transaction_date,
            description=t.description,
            category=t.category,
            student_id=t.student_id,
            recorded_by=t.recorded_by.full_name
        ))
        
    return result

# A simple root endpoint to confirm the API is running
@app.get("/")
def read_root():
    return {"message": "Welcome to the School Finance API"}

