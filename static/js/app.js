// AutoStatus - Mechanic Dashboard Logic

let pendingDeleteOrderId = null;

function updateOrderStatus(orderId, status) {
    const card = document.getElementById(`order-card-${orderId}`);
    if (card) {
        card.style.opacity = '0.6';
    }

    fetch(`/api/work_orders/${orderId}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: status })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            if (status === 'DELIVERED') {
                if (card) {
                    card.style.transition = 'all 0.4s ease';
                    card.style.transform = 'scale(0.95)';
                    card.style.opacity = '0';
                    setTimeout(() => card.remove(), 400);
                }
            } else {
                window.location.reload();
            }
        } else {
            alert('Σφάλμα: ' + data.error);
            if (card) card.style.opacity = '1';
        }
    })
    .catch(err => {
        alert('Σφάλμα επικοινωνίας: ' + err.message);
        if (card) card.style.opacity = '1';
    });
}

function openDamageModal(orderId, plate) {
    document.getElementById('damageOrderId').value = orderId;
    document.getElementById('damageModalPlate').innerText = 'Εύρημα Ζημιάς: ' + plate;
    document.getElementById('damageModal').classList.remove('hidden');
}

function closeDamageModal() {
    document.getElementById('damageModal').classList.add('hidden');
    document.getElementById('damageForm').reset();
}

function submitDamageForm(e) {
    e.preventDefault();
    const orderId = document.getElementById('damageOrderId').value;
    const form = document.getElementById('damageForm');
    const formData = new FormData(form);

    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.innerText = 'Αποστολή...';

    fetch(`/api/work_orders/${orderId}/add_damage`, {
        method: 'POST',
        body: formData
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            closeDamageModal();
            // Automatically prompt to send WhatsApp/Viber link
            openMessageModal(orderId);
        } else {
            alert('Σφάλμα κατά την καταχώριση.');
            submitBtn.disabled = false;
            submitBtn.innerText = 'Αποστολή για Έγκριση';
        }
    })
    .catch(err => {
        alert('Σφάλμα: ' + err.message);
        submitBtn.disabled = false;
        submitBtn.innerText = 'Αποστολή για Έγκριση';
    });
}

function openMessageModal(orderId) {
    const preview = document.getElementById('messagePreview');
    const viberBtn = document.getElementById('viberDirectBtn');
    const waBtn = document.getElementById('whatsappDirectBtn');
    const smsBtn = document.getElementById('smsDirectBtn');

    preview.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Δημιουργία μηνύματος...';
    document.getElementById('messageModal').classList.remove('hidden');

    fetch(`/api/messages/${orderId}`)
    .then(res => res.json())
    .then(data => {
        preview.innerText = data.text;
        viberBtn.href = data.viber_link;
        waBtn.href = data.whatsapp_link;
        if (smsBtn) smsBtn.href = data.sms_link;
    })
    .catch(err => {
        preview.innerText = 'Σφάλμα φόρτωσης: ' + err.message;
    });
}

function closeMessageModal() {
    document.getElementById('messageModal').classList.add('hidden');
}

function copyMessageText() {
    const preview = document.getElementById('messagePreview');
    navigator.clipboard.writeText(preview.innerText).then(() => {
        alert('Το κείμενο αντιγράφηκε στο πρόχειρο!');
    });
}

// Delete Confirmation
function confirmDeleteOrder(orderId, plate) {
    pendingDeleteOrderId = orderId;
    document.getElementById('deleteModalPlateText').innerText = `Θέλετε να διαγράψετε οριστικά την εργασία για το όχημα ${plate};`;
    document.getElementById('deleteModal').classList.remove('hidden');
}

function closeDeleteModal() {
    pendingDeleteOrderId = null;
    document.getElementById('deleteModal').classList.add('hidden');
}

function executeDeleteOrder() {
    if (!pendingDeleteOrderId) return;
    const btn = document.getElementById('confirmDeleteBtn');
    btn.disabled = true;
    btn.innerText = 'Διαγραφή...';

    fetch(`/api/work_orders/${pendingDeleteOrderId}/delete`, {
        method: 'POST'
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            const card = document.getElementById(`order-card-${pendingDeleteOrderId}`);
            if (card) {
                card.remove();
            }
            closeDeleteModal();
            window.location.reload();
        } else {
            alert('Σφάλμα κατά τη διαγραφή.');
        }
        btn.disabled = false;
        btn.innerText = 'Διαγραφή';
    })
    .catch(err => {
        alert('Σφάλμα: ' + err.message);
        btn.disabled = false;
        btn.innerText = 'Διαγραφή';
    });
}
