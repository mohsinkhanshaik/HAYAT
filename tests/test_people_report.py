from app.perception.people_report import people_report


def test_supplied_detection_score_is_explained_without_identifying_a_person():
    value = {'people': [{'box': [11, 79, 1247, 1247], 'score': .9973}], 'count': 1,
             'width': 1254, 'height': 1254, 'threshold': .5, 'device': 'CPU', 'cloud': False,
             'model': 'SSDLite320 MobileNetV3 COCO'}
    text = people_report(value)
    assert '99.73%' in text and '11, 79, 1247, 1247' in text and '1254 × 1254' in text
    assert 'not who someone is' in text and 'No identity matching or internet search' in text
    value['people'].append({'box': [0, 0, 5, 5], 'score': .65})
    assert 'Detection 2: 65.00%' in people_report(value)


def test_no_detections_and_readiness_do_not_make_identity_or_absence_claims():
    assert 'does not establish that nobody' in people_report({'people': [], 'count': 0})
    assert 'does not search for names' in people_report({'installed': True, 'weights_verified': True})
    assert 'Not available' in people_report({'people': [{'score': float('nan')} ]})
