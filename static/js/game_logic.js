document.addEventListener('DOMContentLoaded', () => {
    const socket = io();

    // --- Global Elements ---
    const walletBalanceElement = document.getElementById('wallet-balance');
    const notificationContainer = document.getElementById('notification-container');

    // --- Notification System ---
    window.showNotification = (message, type = 'info') => {
        if (!notificationContainer) return;
        let bgColor, icon;
        switch (type) {
            case 'success': bgColor = 'bg-green-500/80'; icon = '✓'; break;
            case 'error': bgColor = 'bg-red-500/80'; icon = '✖'; break;
            default: bgColor = 'bg-violet-500/80'; icon = 'ℹ'; break;
        }
        const notification = document.createElement('div');
        notification.className = `flex items-center gap-4 ${bgColor} text-white p-4 rounded-lg shadow-lg transform transition-all duration-300 ease-in-out translate-x-full opacity-0`;
        notification.innerHTML = `<p class="font-bold text-xl">${icon}</p><p class="font-medium">${message}</p>`;
        notificationContainer.appendChild(notification);
        gsap.to(notification, { x: 0, opacity: 1, duration: 0.5, ease: 'power2.out' });
        setTimeout(() => {
            gsap.to(notification, { x: '100%', opacity: 0, duration: 0.5, ease: 'power2.in', onComplete: () => notification.remove() });
        }, 4000);
    };

    // --- Game Page Specific Logic ---
    if (document.getElementById('bet-form')) {
        const timerElement = document.getElementById('timer');
        const roundIdElement = document.getElementById('round-id');
        const betForm = document.getElementById('bet-form');
        const betAmountInput = document.getElementById('bet-amount');
        const resultsTableBody = document.getElementById('results-table-body');
        const bettingStatus = document.getElementById('betting-status');
        const betButtons = betForm.querySelectorAll('button[type="submit"]');
        const BETTING_OPEN_UNTIL = 5;

        // Socket.IO Listeners
        socket.on('timer_update', (data) => {
            timerElement.textContent = `00:${String(data.timer).padStart(2, '0')}`;
            roundIdElement.textContent = `#${data.round_id}`;
            if (data.timer <= BETTING_OPEN_UNTIL) {
                bettingStatus.textContent = "Betting Closed";
                bettingStatus.classList.add('text-red-400');
                betButtons.forEach(btn => btn.disabled = true);
            } else {
                bettingStatus.textContent = "Place your bet below";
                bettingStatus.classList.remove('text-red-400');
                betButtons.forEach(btn => btn.disabled = false);
            }
        });

        socket.on('new_result', (data) => {
            const newRow = document.createElement('tr');
            const colorClass = data.result_color === 'green' ? 'bg-green-500' : data.result_color === 'red' ? 'bg-red-500' : 'bg-violet-500';
            newRow.innerHTML = `
                <td class="py-3 text-gray-400">#${data.round_id.slice(-4)}</td>
                <td class="py-3">
                    <span class="px-3 py-1 text-sm font-bold rounded-full text-white ${colorClass}">
                        ${data.result_color.charAt(0).toUpperCase() + data.result_color.slice(1)}
                    </span>
                </td>`;
            resultsTableBody.prepend(newRow);
            gsap.from(newRow, { opacity: 0, y: -20, duration: 0.5 });
            if (resultsTableBody.rows.length > 10) {
                resultsTableBody.deleteRow(-1);
            }
        });

        socket.on('personal_update', (data) => {
            showNotification(data.message, 'success');
            if (data.balance !== undefined) {
                walletBalanceElement.textContent = parseFloat(data.balance).toFixed(2);
            }
        });

        // Bet Form Submission
        betForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const amount = betAmountInput.value;
            const color = e.submitter.value;
            if (!amount || amount <= 0) {
                showNotification('Please enter a valid amount.', 'error');
                return;
            }
            fetch('/bet', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ amount, color }),
            })
            .then(res => res.json())
            .then(data => {
                showNotification(data.message, data.status);
                if (data.status === 'success') {
                    walletBalanceElement.textContent = parseFloat(data.new_balance).toFixed(2);
                    betAmountInput.value = '';
                }
            });
        });
    }

    // --- Shared Modal & Payment Logic ---
    const allModals = document.querySelectorAll('.modal-backdrop');
    const openModal = (modalId) => document.getElementById(modalId)?.classList.remove('modal-hidden');
    const closeModal = (modal) => modal.classList.add('modal-hidden');

    document.getElementById('add-cash-btn')?.addEventListener('click', () => openModal('add-cash-modal'));
    document.getElementById('withdraw-btn')?.addEventListener('click', () => openModal('withdraw-modal'));

    allModals.forEach(modal => {
        modal.querySelector('.close-modal-btn')?.addEventListener('click', () => closeModal(modal));
        modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(modal); });
    });

    // Cashfree Payment Integration
    const cashfree = new Cashfree();
    const paymentBtn = document.getElementById('proceed-to-payment-btn');
    if (paymentBtn) {
        paymentBtn.addEventListener('click', () => {
            const amount = document.getElementById('add-cash-amount').value;
            if (!amount || amount < 10) {
                showNotification('Minimum deposit is ₹10.', 'error');
                return;
            }

            paymentBtn.disabled = true;
            paymentBtn.textContent = 'Processing...';

            fetch('/api/payment/create_order', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ amount }),
            })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success' && data.payment_session_id) {
                    cashfree.checkout({ paymentSessionId: data.payment_session_id });
                } else {
                    showNotification(data.message || 'Could not create payment order.', 'error');
                }
            })
            .finally(() => {
                paymentBtn.disabled = false;
                paymentBtn.textContent = 'Proceed to Pay';
            });
        });
    }
});
