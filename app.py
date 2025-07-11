import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_socketio import SocketIO
from dotenv import load_dotenv
from pymongo import MongoClient, DESCENDING
from werkzeug.security import generate_password_hash, check_password_hash
from bson.objectid import ObjectId
from functools import wraps
import time
import hmac
import hashlib
from threading import Thread, Lock
from datetime import datetime
import random
import math
import requests # Used for Cashfree API calls

# --- Basic Setup ---
load_dotenv()
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'a_fallback_secret_key')
app.static_folder = 'static'
socketio = SocketIO(app, async_mode='threading')

# --- Cashfree API Credentials ---
# IMPORTANT: Store these securely in your environment (.env file)
# The user provided the client ID. The secret is assumed from the image.
# You MUST verify these and add your webhook secret.
app.config['CASHFREE_CLIENT_ID'] = os.getenv('CASHFREE_CLIENT_ID', '1015536f0d803910fdbba902be66355101.cfsk_ma_prod_b8ed73f787f15e033')
app.config['CASHFREE_CLIENT_SECRET'] = os.getenv('CASHFREE_CLIENT_SECRET', '18782098ab17f57_532e434d')
app.config['CASHFREE_API_VERSION'] = '2022-09-01'
app.config['CASHFREE_WEBHOOK_SECRET'] = os.getenv('CASHFREE_WEBHOOK_SECRET', 'YOUR_WEBHOOK_SECRET_HERE')
CASHFREE_API_URL = "https://api.cashfree.com/pg"


# --- Database Connection ---
try:
    mongo_uri = os.getenv('MONGO_URI')
    client = MongoClient(mongo_uri)
    db = client['xdhamaka_db']
    users_collection = db['users']
    bets_collection = db['bets']
    games_collection = db['games']
    admins_collection = db['admins']
    withdrawals_collection = db['withdrawals']
    aviator_games_collection = db['aviator_games']
    aviator_bets_collection = db['aviator_bets']
    # --- New Collections for Advanced Features ---
    preset_results_collection = db['preset_results']
    transactions_collection = db['transactions']
    print("✅ Successfully connected to MongoDB.")

    if admins_collection.count_documents({}) == 0:
        admins_collection.insert_one({
            'username': 'admin',
            'password': generate_password_hash('password')
        })
        print("✅ Default admin user created. Username: admin, Password: password")

except Exception as e:
    print(f"❌ Could not connect to MongoDB. Error: {e}")


# --- Game State & Settings with Locks ---
game_state_lock = Lock()
game_state = {
    "timer": 30,
    "round_id": None
}

aviator_state_lock = Lock()
aviator_game_state = {
    "status": "crashed",
    "round_id": None,
    "crash_point": 1.0,
    "current_multiplier": 1.0,
    "start_time": None,
    "timer": 10
}
AVIATOR_WAIT_TIME = 10
AVIATOR_BREAK_TIME = 5


# --- Transaction Logging Helper ---
def log_transaction(user_id, amount, type, description, associated_id=None):
    """Logs a financial transaction to the database."""
    transactions_collection.insert_one({
        "user_id": user_id,
        "amount": amount,
        "type": type, # e.g., 'deposit', 'withdrawal', 'bet', 'win', 'refund'
        "description": description,
        "associated_id": associated_id, # e.g., round_id or withdrawal_id
        "timestamp": datetime.now()
    })


# --- New, More Profitable Game Algorithms ---

def get_next_color_result():
    """
    Checks for a preset result. If none, generates a result using a more strategic algorithm.
    The algorithm slightly favors colors with fewer total bets to maximize profit.
    """
    preset = preset_results_collection.find_one_and_delete({"game_type": "color", "used": False}, sort=[("created_at", 1)])
    if preset and preset.get('outcome'):
        return preset['outcome']

    # Algorithm: Calculate total bet amounts for each color in the current round
    pipeline = [
        {'$match': {'round_id': game_state['round_id']}},
        {'$group': {'_id': '$color', 'total_amount': {'$sum': '$amount'}}}
    ]
    bet_distribution = {item['_id']: item['total_amount'] for item in bets_collection.aggregate(pipeline)}
    
    total_red = bet_distribution.get('red', 0)
    total_green = bet_distribution.get('green', 0)
    total_violet = bet_distribution.get('violet', 0)

    # Invert the weights. The less money on a color, the higher its chance of winning.
    weight_red = (1 / (total_red + 10)) 
    weight_green = (1 / (total_green + 10))
    weight_violet = (1 / (total_violet + 5)) 

    total_weight = weight_red + weight_green + weight_violet
    prob_red = (weight_red / total_weight) * 100
    prob_green = (weight_green / total_weight) * 100
    prob_violet = 100 - prob_red - prob_green 

    colors = ["red", "green", "violet"]
    chosen_color = random.choices(colors, weights=[prob_red, prob_green, max(5, prob_violet)], k=1)[0]
    return chosen_color

def get_next_aviator_crash_point():
    """
    Checks for a preset crash point. If none, generates one with a profit-focused distribution.
    """
    preset = preset_results_collection.find_one_and_delete({"game_type": "aviator", "used": False}, sort=[("created_at", 1)])
    if preset and preset.get('outcome'):
        return float(preset['outcome'])

    rand = random.random()
    if rand < 0.40: return 1.00
    if rand < 0.75: return round(1.01 + random.random() * 0.98, 2)
    if rand < 0.90: return round(2.00 + random.random() * 2.99, 2)
    if rand < 0.98: return round(5.00 + random.random() * 4.99, 2)
    return round(10.00 + random.random() * 20.00, 2)


# --- Background Game Loops ---
thread = None
aviator_thread = None

@app.before_request
def start_background_threads():
    global thread, aviator_thread
    if thread is None:
        thread = Thread(target=game_loop)
        thread.daemon = True
        thread.start()
    if aviator_thread is None:
        aviator_thread = Thread(target=aviator_game_loop)
        aviator_thread.daemon = True
        aviator_thread.start()

def game_loop():
    while True:
        with game_state_lock:
            game_state["round_id"] = datetime.now().strftime('%Y%m%d%H%M%S')
            game_state["timer"] = 30
        
        while game_state["timer"] > 0:
            socketio.emit('timer_update', {'timer': game_state['timer'], 'round_id': game_state['round_id']})
            with game_state_lock:
                game_state["timer"] -= 1
            time.sleep(1)

        chosen_color = get_next_color_result()
        
        games_collection.insert_one({'round_id': game_state['round_id'], 'result_color': chosen_color, 'timestamp': datetime.now()})
        
        winning_bets = list(bets_collection.find({'round_id': game_state['round_id'], 'color': chosen_color}))
        for bet in winning_bets:
            payout_multiplier = 9 if chosen_color == "violet" else 2
            winnings = bet['amount'] * payout_multiplier
            users_collection.update_one({'_id': bet['user_id']}, {'$inc': {'wallet.balance': winnings}})
            log_transaction(bet['user_id'], winnings, 'win', f"Color game win on {chosen_color}", game_state['round_id'])
            
            user_session_id = get_user_sid(str(bet['user_id']))
            if user_session_id:
                user = users_collection.find_one({'_id': bet['user_id']})
                socketio.emit('personal_update', {'message': f"You won ₹{winnings:.2f}!", 'balance': user['wallet']['balance']}, room=user_session_id)

        socketio.emit('new_result', {'round_id': game_state['round_id'], 'result_color': chosen_color})
        time.sleep(5)

def aviator_game_loop():
    while True:
        with aviator_state_lock:
            aviator_game_state["status"] = "waiting"
            aviator_game_state["round_id"] = datetime.now().strftime('AV%Y%m%d%H%M%S')
            aviator_game_state["crash_point"] = get_next_aviator_crash_point()
        
        for i in range(AVIATOR_WAIT_TIME, 0, -1):
            with aviator_state_lock:
                aviator_game_state["timer"] = i
            socketio.emit('aviator_state_update', {"status": "waiting", "timer": i, "round_id": aviator_game_state["round_id"]})
            socketio.emit('aviator_bets_update', get_live_aviator_bets(aviator_game_state["round_id"]))
            time.sleep(1)

        with aviator_state_lock:
            aviator_game_state["status"] = "flying"
            aviator_game_state["start_time"] = time.time()
        socketio.emit('aviator_state_update', {"status": "flying"})

        while True:
            with aviator_state_lock:
                if aviator_game_state["status"] != "flying": break
                elapsed = time.time() - aviator_game_state["start_time"]
                current_multiplier = round(1.0 + 0.05 * elapsed + 0.05 * (elapsed ** 1.5), 2)
                aviator_game_state["current_multiplier"] = current_multiplier
                if current_multiplier >= aviator_game_state["crash_point"]:
                    break
            
            socketio.emit('aviator_multiplier_update', {"multiplier": current_multiplier})
            time.sleep(0.1)

        with aviator_state_lock:
            aviator_game_state["status"] = "crashed"
            final_multiplier = aviator_game_state["crash_point"]
        
        aviator_games_collection.insert_one({"round_id": aviator_game_state["round_id"], "crash_multiplier": final_multiplier, "timestamp": datetime.now()})
        aviator_bets_collection.update_many({"round_id": aviator_game_state["round_id"], "status": "bet_placed"}, {"$set": {"status": "lost"}})

        socketio.emit('aviator_crash', {"multiplier": final_multiplier})
        socketio.emit('aviator_bets_update', get_live_aviator_bets(aviator_game_state["round_id"]))
        time.sleep(AVIATOR_BREAK_TIME)

# --- User Session & Auth ---
user_sids = {}
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def admin_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        user_sids[session['user_id']] = request.sid

@socketio.on('disconnect')
def handle_disconnect():
    if 'user_id' in session and session['user_id'] in user_sids:
        if user_sids.get(session['user_id']) == request.sid:
            del user_sids[session['user_id']]

def get_user_sid(user_id):
    return user_sids.get(user_id)

def get_live_aviator_bets(round_id):
    bets_cursor = aviator_bets_collection.find({'round_id': round_id}, {'_id': 0, 'user_id': 1, 'amount': 1, 'status': 1, 'cashout_multiplier': 1, 'winnings': 1})
    live_bets = []
    for bet in bets_cursor:
        user = users_collection.find_one({'_id': bet['user_id']}, {'mobile': 1})
        bet['user'] = f"***{user['mobile'][-4:]}" if user else '***????'
        del bet['user_id']
        live_bets.append(bet)
    return live_bets

# --- Main Routes ---
@app.route('/')
def index():
    if 'user_id' in session: return redirect(url_for('hub'))
    return render_template('index.html')

@app.route('/hub')
@login_required
def hub():
    return render_template('hub.html')

@app.route('/game')
@login_required
def game():
    user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
    recent_games = list(games_collection.find().sort('timestamp', DESCENDING).limit(10))
    return render_template('game.html', user=user, recent_games=recent_games)

@app.route('/aviator')
@login_required
def aviator():
    user_id = ObjectId(session['user_id'])
    user = users_collection.find_one({'_id': user_id})
    current_bet = aviator_bets_collection.find_one({'user_id': user_id, 'round_id': aviator_game_state.get("round_id")})
    if current_bet: current_bet['_id'] = str(current_bet['_id'])
    recent_games = list(aviator_games_collection.find().sort('timestamp', DESCENDING).limit(10))
    return render_template('aviator.html', user=user, recent_games=recent_games, current_bet=current_bet)

# --- Auth Routes ---
@app.route('/register', methods=['POST'])
def register():
    mobile = request.form.get('mobile')
    password = request.form.get('password')
    if not mobile or not password:
        flash('Mobile and password are required.', 'error')
        return redirect(url_for('index'))
    if users_collection.find_one({'mobile': mobile}):
        flash('This mobile number is already registered.', 'error')
        return redirect(url_for('index'))

    hashed_password = generate_password_hash(password)
    new_user = {'mobile': mobile, 'password': hashed_password, 'wallet': {'balance': 100.0}, 'status': 'active', 'created_at': datetime.now()}
    user_id = users_collection.insert_one(new_user).inserted_id
    log_transaction(user_id, 100.0, 'deposit', 'Initial registration bonus')
    session['user_id'] = str(user_id)
    return redirect(url_for('hub'))

@app.route('/login', methods=['POST'])
def login():
    mobile = request.form.get('mobile')
    password = request.form.get('password')
    user = users_collection.find_one({'mobile': mobile})
    if user and user.get('status') == 'blocked':
        flash('Your account has been suspended.', 'error')
        return redirect(url_for('index'))
    if user and check_password_hash(user['password'], password):
        session['user_id'] = str(user['_id'])
        return redirect(url_for('hub'))
    else:
        flash('Invalid mobile number or password.', 'error')
        return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))

# --- Game API Routes ---
@app.route('/bet', methods=['POST'])
@login_required
def place_bet():
    data = request.get_json()
    user_id = ObjectId(session['user_id'])
    try:
        amount = float(data.get('amount'))
        color = data.get('color')
        if color not in ['red', 'green', 'violet'] or amount <= 0: raise ValueError("Invalid data")
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid bet data."}), 400

    if game_state['timer'] <= 5:
        return jsonify({"status": "error", "message": "Betting is closed for this round."})

    user = users_collection.find_one({'_id': user_id})
    if user['wallet']['balance'] < amount:
        return jsonify({"status": "error", "message": "Insufficient funds."})
    
    users_collection.update_one({'_id': user_id}, {'$inc': {'wallet.balance': -amount}})
    log_transaction(user_id, -amount, 'bet', f"Color game bet on {color}", game_state['round_id'])
    bets_collection.insert_one({'user_id': user_id, 'round_id': game_state['round_id'], 'color': color, 'amount': amount, 'timestamp': datetime.now()})

    new_balance = user['wallet']['balance'] - amount
    return jsonify({"status": "success", "message": f"Bet of ₹{amount:.2f} on {color} placed!", "new_balance": new_balance})

@app.route('/api/aviator/bet', methods=['POST'])
@login_required
def place_aviator_bet():
    if aviator_game_state['status'] != 'waiting':
        return jsonify({"status": "error", "message": "Betting is currently closed."}), 400
    data = request.get_json()
    user_id = ObjectId(session['user_id'])
    try:
        amount = float(data.get('amount'))
        if amount <= 0: raise ValueError("Invalid amount")
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid bet data."}), 400

    user = users_collection.find_one({'_id': user_id})
    if user['wallet']['balance'] < amount:
        return jsonify({"status": "error", "message": "Insufficient funds."}), 400
    if aviator_bets_collection.find_one({"user_id": user_id, "round_id": aviator_game_state['round_id']}):
        return jsonify({"status": "error", "message": "You have already placed a bet for this round."}), 400

    users_collection.update_one({'_id': user_id}, {'$inc': {'wallet.balance': -amount}})
    log_transaction(user_id, -amount, 'bet', "Aviator bet", aviator_game_state['round_id'])
    aviator_bets_collection.insert_one({'user_id': user_id, 'round_id': aviator_game_state['round_id'], 'amount': amount, 'status': 'bet_placed', 'timestamp': datetime.now()})
    
    socketio.emit('aviator_bets_update', get_live_aviator_bets(aviator_game_state["round_id"]))
    new_balance = user['wallet']['balance'] - amount
    return jsonify({"status": "success", "message": f"Bet of ₹{amount:.2f} placed!", "new_balance": new_balance})

@app.route('/api/aviator/cancel', methods=['POST'])
@login_required
def cancel_aviator_bet():
    """Allows a user to cancel their bet during the 'waiting' phase."""
    with aviator_state_lock:
        if aviator_game_state['status'] != 'waiting':
            return jsonify({"status": "error", "message": "Cannot cancel bet now. The game has already started."}), 400
        round_id = aviator_game_state['round_id']
    
    user_id = ObjectId(session['user_id'])
    
    bet_to_cancel = aviator_bets_collection.find_one_and_delete({
        'user_id': user_id, 'round_id': round_id, 'status': 'bet_placed'
    })

    if not bet_to_cancel:
        return jsonify({"status": "error", "message": "No active bet found to cancel."}), 400
    
    refund_amount = bet_to_cancel['amount']
    users_collection.update_one({'_id': user_id}, {'$inc': {'wallet.balance': refund_amount}})
    log_transaction(user_id, refund_amount, 'refund', 'Aviator bet canceled', bet_to_cancel['_id'])
    
    socketio.emit('aviator_bets_update', get_live_aviator_bets(round_id))
    
    user = users_collection.find_one({'_id': user_id})
    return jsonify({
        "status": "success",
        "message": "Your bet has been canceled and the amount refunded.",
        "new_balance": user['wallet']['balance']
    })

@app.route('/api/aviator/cashout', methods=['POST'])
@login_required
def cashout_aviator():
    with aviator_state_lock:
        if aviator_game_state['status'] != 'flying':
            return jsonify({"status": "error", "message": "Cannot cash out now."}), 400
        cashout_multiplier = aviator_game_state['current_multiplier']
        round_id = aviator_game_state['round_id']
    
    user_id = ObjectId(session['user_id'])
    
    bet_to_cashout = aviator_bets_collection.find_one({'user_id': user_id, 'round_id': round_id, 'status': 'bet_placed'})
    if not bet_to_cashout:
        return jsonify({"status": "error", "message": "No active bet found to cash out."}), 400
    
    winnings = bet_to_cashout['amount'] * cashout_multiplier
    aviator_bets_collection.update_one({'_id': bet_to_cashout['_id']}, {'$set': {'status': 'cashed_out', 'cashout_multiplier': cashout_multiplier, 'winnings': winnings}})
    users_collection.update_one({'_id': user_id}, {'$inc': {'wallet.balance': winnings}})
    log_transaction(user_id, winnings, 'win', f"Aviator cashout @{cashout_multiplier:.2f}x", round_id)
    
    socketio.emit('aviator_bets_update', get_live_aviator_bets(round_id))
    user = users_collection.find_one({'_id': user_id})
    return jsonify({"status": "success", "message": f"Cashed out for ₹{winnings:.2f}!", "new_balance": user['wallet']['balance']})

# --- Payment Routes (Cashfree Integration) ---
@app.route('/api/payment/create_order', methods=['POST'])
@login_required
def create_payment_order():
    data = request.get_json()
    try:
        amount = float(data.get('amount'))
        if amount < 10: raise ValueError("Min amount is 10")
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid amount."}), 400

    user_id = session['user_id']
    user = users_collection.find_one({'_id': ObjectId(user_id)})
    order_id = f"order_{int(time.time())}_{user_id}"

    headers = {
        "Content-Type": "application/json",
        "x-api-version": app.config['CASHFREE_API_VERSION'],
        "x-client-id": app.config['CASHFREE_CLIENT_ID'],
        "x-client-secret": app.config['CASHFREE_CLIENT_SECRET']
    }
    payload = {
        "order_id": order_id,
        "order_amount": amount,
        "order_currency": "INR",
        "order_note": "Deposit to 9xdhamaka",
        "customer_details": {
            "customer_id": str(user['_id']),
            "customer_phone": user['mobile']
        },
        "order_meta": {"return_url": url_for('game', _external=True) + f"?order_id={{{order_id}}}"}
    }
    
    try:
        response = requests.post(f"{CASHFREE_API_URL}/orders", json=payload, headers=headers)
        response.raise_for_status()
        order_data = response.json()
        return jsonify({"status": "success", "payment_session_id": order_data.get('payment_session_id')})
    except requests.exceptions.RequestException as e:
        print(f"Cashfree API Error: {e}")
        return jsonify({"status": "error", "message": "Could not connect to payment gateway."}), 500

@app.route('/api/payment/webhook', methods=['POST'])
def cashfree_webhook():
    # --- Step 1: Security Verification (Stays the Same) ---
    received_signature = request.headers.get('x-webhook-signature')
    timestamp = request.headers.get('x-webhook-timestamp')
    payload = request.get_data(as_text=True)

    if not received_signature or not timestamp:
        return jsonify({"status": "error", "message": "Missing headers"}), 400

    secret = app.config['CASHFREE_WEBHOOK_SECRET']
    message = timestamp + payload
    computed_signature = hmac.new(
        key=secret.encode('utf-8'),
        msg=message.encode('utf-8'),
        digestmod=hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_signature, received_signature):
        print("❌ Webhook signature verification failed!")
        return jsonify({"status": "error", "message": "Invalid signature"}), 401
    
    # --- Step 2: Handle Different Event Types ---
    print("✅ Webhook signature verified successfully.")
    data = request.get_json()
    order = data.get('data', {}).get('order', {})
    
    # If the payload doesn't contain order data, we can't process it.
    if not order:
        print(f"Webhook received non-order event: {data.get('type')}")
        return jsonify({"status": "ok", "message": "Non-order event received"}), 200

    order_status = order.get('order_status')
    user_id_str = order.get('customer_details', {}).get('customer_id')
    
    # --- IF PAYMENT IS SUCCESSFUL ---
    if order_status == 'PAID':
        amount = float(order['order_amount'])
        user_id = ObjectId(user_id_str)
        
        # Prevent processing the same successful order twice
        if transactions_collection.find_one({"type": "deposit", "associated_id": order['order_id']}):
            return jsonify({"status": "already_processed"}), 200

        # Add money to user's wallet
        users_collection.update_one({'_id': user_id}, {'$inc': {'wallet.balance': amount}})
        log_transaction(user_id, amount, 'deposit', 'Deposit via Cashfree', order['order_id'])
        
        # Notify the user via WebSocket
        user_session_id = get_user_sid(str(user_id))
        if user_session_id:
            user = users_collection.find_one({'_id': user_id})
            socketio.emit('personal_update', {'message': f"₹{amount:.2f} added to your wallet.", 'balance': user['wallet']['balance']}, room=user_session_id)
            
    # --- ELSE IF PAYMENT FAILED ---
    elif order_status == 'FAILED':
        amount = float(order['order_amount'])
        user_id = ObjectId(user_id_str)
        log_transaction(user_id, amount, 'deposit_failed', f"Payment failed for order {order['order_id']}", order['order_id'])
        
        # Notify the user that the payment failed
        user_session_id = get_user_sid(str(user_id))
        if user_session_id:
            socketio.emit('personal_update', {'message': f"Your payment of ₹{amount:.2f} failed. Please try again."}, room=user_session_id)
            
    # --- ELSE IF A REFUND WAS PROCESSED ---
    elif order_status == 'REFUNDED':
        amount = float(order['order_amount']) # Or use a specific refund amount if available
        user_id = ObjectId(user_id_str)
        log_transaction(user_id, -amount, 'refund_processed', f"Refund processed for order {order['order_id']}", order['order_id'])

        # Notify the user their refund is complete
        user_session_id = get_user_sid(str(user_id))
        if user_session_id:
            socketio.emit('personal_update', {'message': f"Your refund for order {order['order_id']} has been processed."}, room=user_session_id)

    else:
        # Log any other statuses you are not yet handling
        print(f"Received unhandled order status: {order_status}")
            
    return jsonify({"status": "ok"}), 200


@app.route('/request_withdrawal', methods=['POST'])
@login_required
def request_withdrawal():
    try:
        amount = float(request.form.get('amount'))
        upi_id = request.form.get('upi_id')
        if not upi_id or amount < 100: raise ValueError("Invalid data")
    except (ValueError, TypeError):
        flash("Invalid data. Minimum withdrawal is ₹100.", "error")
        return redirect(request.referrer or url_for('hub'))
        
    user_id = ObjectId(session['user_id'])
    user = users_collection.find_one({'_id': user_id})
    if user['wallet']['balance'] < amount:
        flash("Insufficient funds.", "error")
    else:
        users_collection.update_one({'_id': user_id}, {'$inc': {'wallet.balance': -amount}})
        req_id = withdrawals_collection.insert_one({'user_id': user_id, 'amount': amount, 'upi_id': upi_id, 'status': 'pending', 'requested_at': datetime.now()}).inserted_id
        log_transaction(user_id, -amount, 'withdrawal_request', f"Withdrawal request to {upi_id}", req_id)
        flash("Withdrawal request submitted. It will be processed within 1-2 business days.", "success")
    return redirect(request.referrer or url_for('hub'))

# --- ADMIN SECTION ---
@app.route('/admin')
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if 'admin_id' in session: return redirect(url_for('admin_dashboard', page='dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        admin = admins_collection.find_one({'username': username})
        if admin and check_password_hash(admin['password'], password):
            session['admin_id'] = str(admin['_id'])
            return redirect(url_for('admin_dashboard', page='dashboard'))
        else:
            flash('Invalid admin credentials.', 'error')
    return render_template('admin.html', page='login')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_id', None)
    return redirect(url_for('admin_login'))

@app.route('/admin/<page>')
@admin_login_required
def admin_dashboard(page):
    data = {}
    if page == 'dashboard':
        total_bets = sum(t['amount'] for t in transactions_collection.find({'type': 'bet'}))
        total_wins = sum(t['amount'] for t in transactions_collection.find({'type': 'win'}))
        total_deposits = sum(t['amount'] for t in transactions_collection.find({'type': 'deposit'}))
        approved_withdrawals = withdrawals_collection.find({'status': 'approved'})
        total_withdrawals = sum(w['amount'] for w in approved_withdrawals)
        
        data['total_users'] = users_collection.count_documents({})
        data['pending_withdrawals'] = withdrawals_collection.count_documents({'status': 'pending'})
        data['net_profit'] = abs(total_bets) - total_wins
        data['total_deposits'] = total_deposits
        data['total_withdrawals'] = total_withdrawals

    elif page == 'users':
        data['all_users'] = list(users_collection.find())
    elif page == 'game_results':
        data['game_history'] = list(games_collection.find().sort('timestamp', DESCENDING).limit(100))
    elif page == 'aviator_history':
        data['aviator_history'] = list(aviator_games_collection.find().sort('timestamp', DESCENDING).limit(100))
    elif page == 'withdrawals':
        pipeline = [{'$sort': {'requested_at': -1}}, {'$lookup': {'from': 'users', 'localField': 'user_id', 'foreignField': '_id', 'as': 'user_details'}}, {'$unwind': '$user_details'}]
        data['requests'] = list(withdrawals_collection.aggregate(pipeline))
    elif page == 'control':
        data['preset_colors'] = list(preset_results_collection.find({"game_type": "color"}).sort('created_at', 1))
        data['preset_aviators'] = list(preset_results_collection.find({"game_type": "aviator"}).sort('created_at', 1))

    template_to_render = f"admin_partials/{page}.html"
    if not os.path.exists(os.path.join(app.template_folder, template_to_render)):
        template_to_render = 'admin_partials/empty.html'
    return render_template('admin.html', page=page, data=data, partial_template=template_to_render)

# --- ADMIN ACTION ROUTES ---
@app.route('/admin/action/set_presets', methods=['POST'])
@admin_login_required
def admin_set_presets():
    game_type = request.form.get('game_type')
    if game_type == 'color':
        outcomes = request.form.getlist('color_outcome')
        preset_results_collection.delete_many({"game_type": "color"})
        for outcome in outcomes:
            if outcome:
                preset_results_collection.insert_one({"game_type": "color", "outcome": outcome, "used": False, "created_at": datetime.now()})
    elif game_type == 'aviator':
        outcomes = request.form.getlist('aviator_outcome')
        preset_results_collection.delete_many({"game_type": "aviator"})
        for outcome in outcomes:
            if outcome:
                try:
                    val = float(outcome)
                    if val >= 1.0:
                        preset_results_collection.insert_one({"game_type": "aviator", "outcome": val, "used": False, "created_at": datetime.now()})
                except ValueError:
                    flash(f"Invalid aviator value: {outcome}", "error")
    flash("Presets have been saved.", "success")
    return redirect(url_for('admin_dashboard', page='control'))

@app.route('/admin/action/process_withdrawal/<request_id>', methods=['POST'])
@admin_login_required
def admin_process_withdrawal(request_id):
    action = request.form.get('action')
    withdrawal_req = withdrawals_collection.find_one({'_id': ObjectId(request_id)})
    if not withdrawal_req:
        flash("Request not found.", "error")
        return redirect(url_for('admin_dashboard', page='withdrawals'))

    if action == 'approve':
        withdrawals_collection.update_one({'_id': ObjectId(request_id)}, {'$set': {'status': 'approved', 'processed_at': datetime.now()}})
        log_transaction(withdrawal_req['user_id'], -withdrawal_req['amount'], 'withdrawal_approved', 'Withdrawal approved by admin', ObjectId(request_id))
        flash("Withdrawal approved.", "success")
    elif action == 'reject':
        withdrawals_collection.update_one({'_id': ObjectId(request_id)}, {'$set': {'status': 'rejected', 'processed_at': datetime.now()}})
        users_collection.update_one({'_id': withdrawal_req['user_id']}, {'$inc': {'wallet.balance': withdrawal_req['amount']}})
        log_transaction(withdrawal_req['user_id'], withdrawal_req['amount'], 'withdrawal_refund', 'Withdrawal rejected and refunded', ObjectId(request_id))
        flash("Withdrawal rejected and amount refunded.", "warning")
    return redirect(url_for('admin_dashboard', page='withdrawals'))

@app.route('/admin/action/toggle_user_status/<user_id>', methods=['POST'])
@admin_login_required
def admin_toggle_user_status(user_id):
    user = users_collection.find_one({'_id': ObjectId(user_id)})
    if user:
        new_status = 'blocked' if user.get('status', 'active') == 'active' else 'active'
        users_collection.update_one({'_id': ObjectId(user_id)}, {'$set': {'status': new_status}})
        flash(f"User status changed to {new_status}.", "success")
    return redirect(url_for('admin_dashboard', page='users'))

@app.route('/admin/action/add_bonus', methods=['POST'])
@admin_login_required
def admin_add_bonus():
    user_id = ObjectId(request.form.get('user_id'))
    try:
        bonus_amount = float(request.form.get('bonus_amount'))
        if bonus_amount > 0:
            users_collection.update_one({'_id': user_id}, {'$inc': {'wallet.balance': bonus_amount}})
            log_transaction(user_id, bonus_amount, 'deposit', f"Admin bonus of {bonus_amount}", session.get('admin_id'))
            flash(f"Added ₹{bonus_amount:.2f} bonus.", "success")
    except (ValueError, TypeError):
        flash("Invalid bonus amount.", "error")
    return redirect(url_for('admin_dashboard', page='users'))


# --- Main Execution ---
if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000, allow_unsafe_werkzeug=True)