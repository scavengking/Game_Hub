document.addEventListener('DOMContentLoaded', () => {
    const socket = io();

    // --- Global Elements ---
    const walletBalanceElement = document.getElementById('wallet-balance');
    const notificationContainer = document.getElementById('notification-container');

    // --- Notification System ---
    window.showNotification = (message, type = 'info') => {
        if (!notificationContainer) return;

        let bgColor, textColor, icon;
        switch (type) {
            case 'success':
                bgColor = 'bg-green-500/80'; textColor = 'text-white';
                icon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`;
                break;
            case 'error':
                bgColor = 'bg-red-500/80'; textColor = 'text-white';
                icon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`;
                break;
            default: // info/warning
                bgColor = 'bg-violet-500/80'; textColor = 'text-white';
                icon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`;
                break;
        }

        const notification = document.createElement('div');
        notification.className = `flex items-center gap-4 ${bgColor} ${textColor} p-4 rounded-lg shadow-lg transform transition-all duration-300 ease-in-out translate-x-full opacity-0`;
        notification.innerHTML = `
            ${icon}
            <p class="font-medium">${message}</p>
        `;
        notificationContainer.appendChild(notification);

        // Animate in
        gsap.to(notification, { x: 0, opacity: 1, duration: 0.5, ease: 'power2.out' });

        // Auto-dismiss
        setTimeout(() => {
            gsap.to(notification, { 
                x: '100%', 
                opacity: 0, 
                duration: 0.5, 
                ease: 'power2.in', 
                onComplete: () => notification.remove() 
            });
        }, 4000);
    };


    // --- Game Page Logic ---
    if (document.getElementById('bet-form')) {
        const timerElement = document.getElementById('timer');
        const roundIdElement = document.getElementById('round-id');
        const betForm = document.getElementById('bet-form');
        const betAmountInput = document.getElementById('bet-amount');
        const resultsTableBody = document.getElementById('results-table-body');
        const bettingStatus = document.getElementById('betting-status');
        const betButtons = betForm.querySelectorAll('button[type="submit"]');

        const BETTING_OPEN_UNTIL = 5; // Timer value when betting closes

        // Socket.IO Listeners
        socket.on('timer_update', (data) => {
            const seconds = String(data.timer).padStart(2, '0');
            const newTime = `00:${seconds}`;

            if (timerElement.textContent !== newTime) {
                gsap.to(timerElement, { scale: 1.05, duration: 0.2, onComplete: () => {
                    timerElement.textContent = newTime;
                    gsap.to(timerElement, { scale: 1, duration: 0.4 });
                }});
            }
            
            roundIdElement.textContent = `#${data.round_id}`;
            
            if(data.timer <= BETTING_OPEN_UNTIL) {
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
                </td>
            `;
            
            resultsTableBody.prepend(newRow);
            gsap.from(newRow, { opacity: 0, y: -20, duration: 0.5 });

            if (resultsTableBody.rows.length > 10) {
                gsap.to(resultsTableBody.rows[resultsTableBody.rows.length - 1], {
                    opacity: 0,
                    duration: 0.5,
                    onComplete: () => resultsTableBody.deleteRow(-1)
                });
            }
        });

        socket.on('personal_update', (data) => {
            showNotification(data.message, 'success');
            if (data.balance !== undefined && walletBalanceElement) {
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
                body: JSON.stringify({ amount: amount, color: color }),
            })
            .then(response => response.json())
            .then(data => {
                showNotification(data.message, data.status);
                if (data.status === 'success') {
                    walletBalanceElement.textContent = parseFloat(data.new_balance).toFixed(2);
                    betAmountInput.value = '';
                }
            })
            .catch(error => {
                console.error('Error placing bet:', error);
                showNotification('An error occurred. Please try again.', 'error');
            });
        });
    }

    // --- Modal Logic ---
    const allModals = document.querySelectorAll('.modal-backdrop');
    
    const openModal = (modalId) => {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.classList.remove('modal-hidden');
        }
    };

    const closeModal = (modal) => {
        modal.classList.add('modal-hidden');
    };

    document.getElementById('add-cash-btn')?.addEventListener('click', () => openModal('add-cash-modal'));
    document.getElementById('withdraw-btn')?.addEventListener('click', () => openModal('withdraw-modal'));

    allModals.forEach(modal => {
        // Close with the cancel button
        modal.querySelector('.close-modal-btn')?.addEventListener('click', () => closeModal(modal));
        // Close by clicking the backdrop
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                closeModal(modal);
            }
        });
    });
});
