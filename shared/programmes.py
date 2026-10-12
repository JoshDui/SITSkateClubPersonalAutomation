"""SIT clusters and degree programmes for special-event registration.

Official names come from the OSE "Student Training Attendance Form"
(Apr-OSE-FRM-042, Programme List sheet), so exported rows can be pasted into
that form's Cluster / Degree Programme dropdown columns unchanged. Cleaned:
duplicates removed, trailing spaces stripped, and the mis-encoded dash in the
BSN–MSN entry fixed. Each entry pairs the official name with a short label
that fits on a phone-width Telegram button.

Buttons reference a programme by its index in the cluster's list (sorted by
label), keeping callback data well under Telegram's 64-byte limit.
"""
from __future__ import annotations

CLUSTER_ORDER = ('BCD', 'ENG', 'FCB', 'ICT', 'HSS')

_PROGRAMMES = {
    'BCD': [
        ('Bachelor of Accountancy with Honours', 'Accountancy'),
        ('Bachelor of Science with Honours in Air Transport Management', 'Air Transport Management'),
        ('Bachelor of Hospitality Business with Honours', 'Hospitality Business'),
        ('Bachelor of Business Administration in Food Business Management (Baking and Pastry Arts)',
         'Food Business Mgmt (Baking & Pastry)'),
        ('Bachelor of Business Administration in Food Business Management (Culinary Arts)',
         'Food Business Mgmt (Culinary Arts)'),
        ('Bachelor of Science with Honours in Digital Communications and Integrated Media',
         'Digital Comms & Integrated Media'),
    ],
    'ENG': [
        ('Bachelor of Engineering with Honours in Sustainable Infrastructure Engineering (Building Services)',
         'Sustainable Infra Eng (Building Services)'),
        ('Bachelor of Engineering with Honours in Sustainable Built Environment (SBE)',
         'Sustainable Built Environment'),
        ('Bachelor of Engineering with Honours in Sustainable Infrastructure Engineering (Land)',
         'Sustainable Infra Eng (Land)'),
        ('Bachelor of Engineering with Honours in Engineering Systems', 'Engineering Systems'),
        ('Master of Science in Mechanical Engineering', 'MSc Mechanical Engineering'),
        ('Master of Science in Electrical and Electronic Engineering', 'MSc Electrical & Electronic Eng'),
        ('Bachelor of Engineering with Honours in Electrical Power Engineering', 'Electrical Power Engineering'),
        ('Bachelor of Engineering with Honours in Aircraft Systems Engineering', 'Aircraft Systems Engineering'),
        ('Bachelor of Engineering with Honours in Aerospace Engineering', 'Aerospace Engineering'),
        ('Master of Science in Civil Engineering', 'MSc Civil Engineering'),
        ('Bachelor of Engineering with Honours in Civil Engineering', 'Civil Engineering'),
        ('Bachelor of Engineering with Honours in Mechanical Design and Manufacturing Engineering',
         'Mech Design & Manufacturing Eng'),
        ('Bachelor of Engineering with Honours in Naval Architecture and Marine Engineering',
         'Naval Architecture & Marine Eng'),
        ('Bachelor of Engineering with Honours in Mechanical Engineering', 'Mechanical Engineering'),
        ('Bachelor of Engineering with Honours in Electronics and Data Engineering',
         'Electronics & Data Engineering'),
        ('Bachelor of Science in Electrical Engineering & Infocomm Technology', 'Electrical Eng & Infocomm Tech'),
        ('Bachelor of Engineering with Honours in Systems Engineering (ElectroMechanical Systems)',
         'Systems Eng (ElectroMechanical)'),
        ('Bachelor of Engineering with Honours in Mechatronics Systems', 'Mechatronics Systems'),
        ('Bachelor of Engineering with Honours in Robotics Systems', 'Robotics Systems'),
        ('Bachelor of Science with Honours in Digital Supply Chain', 'Digital Supply Chain'),
        ('Electrical and Electronic Engineering programme on the Competency-based Stackable '
         'Micro-credential (CSM) pathway', 'Electrical & Electronic Eng (CSM)'),
        ('Infrastructure and Systems Engineering programme on the Competency-based Stackable '
         'Micro-credential (CSM) pathway', 'Infrastructure & Systems Eng (CSM)'),
    ],
    'FCB': [
        ('Bachelor of Engineering with Honours in Pharmaceutical Engineering', 'Pharmaceutical Engineering'),
        ('Bachelor of Food Technology with Honours', 'Food Technology'),
        ('Master of Science in Chemical Engineering', 'MSc Chemical Engineering'),
        ('Bachelor of Engineering with Honours in Chemical Engineering', 'Chemical Engineering (BEng)'),
        ('Bachelor of Science in Chemical Engineering', 'Chemical Engineering (BSc)'),
        ('Master of Engineering', 'Master of Engineering'),
        ('Doctorate of Engineering', 'Doctorate of Engineering'),
    ],
    'ICT': [
        ('Bachelor of Engineering with Honours in Information and Communications Technology majoring in '
         'Software Engineering', 'ICT (Software Engineering)'),
        ('Bachelor of Engineering with Honours in Information & Communications Technology majoring in '
         'Information Security', 'ICT (Information Security)'),
        ('Bachelor of Engineering with Honours in Computer Engineering', 'Computer Engineering'),
        ('Bachelor of Science with Honours in Computing Science', 'Computing Science'),
        ('Bachelor of Science with Honours in Computer Science in Interactive Media and Game Development',
         'CS Interactive Media & Game Dev'),
        ('Bachelor of Science with Honours in Computer Science in Real-Time Interactive Simulation',
         'CS Real-Time Interactive Sim (Hons)'),
        ('Bachelor of Science in Computer Science in Real-Time Interactive Simulation',
         'CS Real-Time Interactive Sim'),
        ('Bachelor of Science in Computer Science and Game Design', 'CS and Game Design'),
        ('Bachelor of Arts in Game Design', 'Game Design (BA)'),
        ('Bachelor of Fine Arts in Digital Art and Animation', 'Digital Art & Animation'),
        ('Bachelor of Arts in User Experience and Game Design', 'UX & Game Design'),
        ('Bachelor of Science with Honours in Applied Artificial Intelligence', 'Applied AI'),
        ('Bachelor of Science with Honours in Applied Computing', 'Applied Computing'),
        ('ICT Programmes on the Competency-based Stackable Micro-credential (CSM) pathway',
         'ICT Programmes (CSM)'),
    ],
    'HSS': [
        ('Bachelor of Science with Honours in Occupational Therapy', 'Occupational Therapy'),
        ('Bachelor of Science with Honours in Speech and Language Therapy', 'Speech & Language Therapy'),
        ('Bachelor of Science with Honours in Diagnostic Radiography', 'Diagnostic Radiography'),
        ('Bachelor of Science with Honours in Radiation Therapy', 'Radiation Therapy'),
        ('Bachelor of Science with Honours in Dietetics and Nutrition', 'Dietetics & Nutrition'),
        ('Bachelor of Science with Honours in Physiotherapy', 'Physiotherapy'),
        ('Bachelor of Science with Honours in Nursing', 'Nursing'),
        ('Bachelor of Science in Nursing (BSN) – Master of Science in Nursing (MSN)', 'Nursing (BSN–MSN)'),
        ('Master of Clinical Research in Health Sciences', 'Master of Clinical Research'),
        ('Doctor of Clinical Research in Health Sciences', 'Doctor of Clinical Research'),
        ('Master of Health Sciences', 'Master of Health Sciences'),
    ],
}

# Sorted by label so the buttons read alphabetically.
PROGRAMMES = {cluster: sorted(items, key=lambda item: item[1].casefold())
              for cluster, items in _PROGRAMMES.items()}


def programme(cluster, index):
    """(official name, label) for a button index, or ValueError."""
    items = PROGRAMMES[cluster]
    if not 0 <= index < len(items):
        raise ValueError('Unknown programme')
    return items[index]
