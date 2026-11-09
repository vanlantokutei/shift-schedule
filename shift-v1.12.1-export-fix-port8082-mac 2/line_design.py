"""Betoya LINE cards; all dates and links come from the live Kibo window."""
from datetime import timedelta
PHOTO = 'https://shift-schedule-1fk2.onrender.com/static/line/pho-betoya.jpg'
GREEN = '#075B3B'
CREAM = '#FAF8F2'
GOLD = '#C7B68D'
LOGO = 'https://shift-schedule-1fk2.onrender.com/static/line/logo-betoya.png'


def action(label, uri):
    return {'type': 'button', 'style': 'primary', 'color': GREEN,
            'action': {'type': 'uri', 'label': label, 'uri': uri}}


def card(alt, title, text, signup_url):
    return {'type': 'flex', 'altText': alt[:400], 'contents': {
        'type': 'bubble', 'size': 'mega',
        'hero': {'type': 'box', 'layout': 'vertical', 'paddingAll': '0px', 'contents': [
            {'type': 'image', 'url': PHOTO, 'size': 'full', 'aspectRatio': '20:13',
             'aspectMode': 'fit', 'backgroundColor': '#FFFFFF'},
            {'type': 'image', 'url': LOGO, 'size': 'full', 'aspectRatio': '953:327',
             'aspectMode': 'fit', 'position': 'absolute', 'offsetTop': '10px',
             'offsetEnd': '12px', 'width': '88px', 'height': '31px'}]},
        'body': {'type': 'box', 'layout': 'vertical', 'spacing': 'md', 'paddingAll': '18px', 'contents': [
            {'type': 'text', 'text': 'ベト屋築地店', 'color': GREEN, 'size': 'xs', 'weight': 'bold'},
            {'type': 'text', 'text': title, 'weight': 'bold', 'size': 'xl', 'wrap': True, 'color': '#20342B'},
            {'type': 'text', 'text': text, 'size': 'sm', 'wrap': True, 'color': '#52645A'}]},
        'footer': {'type': 'box', 'layout': 'vertical', 'spacing': 'sm', 'contents': [
            action('Đăng ký kibo', signup_url),
            {'type': 'text', 'text': 'Cảm ơn mọi người! 🌿', 'size': 'xs',
             'align': 'center', 'wrap': True, 'color': '#6B7B72'}]},
        'styles': {'body': {'backgroundColor': CREAM}, 'footer': {'backgroundColor': CREAM}}}}


def reminder_card(text, signup_url, target, deadline):
    result = card(text, ('GỬI THỬ · ' if text.startswith('GỬI THỬ') else '') + 'Đăng ký lịch tuần tới',
                  'Mọi người gửi lịch rảnh nhé!', signup_url)
    body = result['contents']['body']['contents']
    body.insert(2, {'type': 'text', 'text': '希望シフト', 'align': 'center', 'size': 'md', 'color': '#52645A'})
    body.insert(3, {'type': 'box', 'layout': 'vertical', 'paddingAll': '10px', 'cornerRadius': 'xxl',
                   'borderWidth': '1px', 'borderColor': GOLD, 'contents': [
                       {'type': 'text', 'text': f'{target:%d/%m} – {(target + timedelta(days=6)):%d/%m}',
                        'weight': 'bold', 'align': 'center', 'size': 'xxl', 'color': GREEN}]})
    days = ['Thứ Hai','Thứ Ba','Thứ Tư','Thứ Năm','Thứ Sáu','Thứ Bảy','Chủ Nhật']
    body.append({'type': 'box', 'layout': 'vertical', 'paddingAll': '12px', 'cornerRadius': '14px',
                 'backgroundColor': '#E5EBD9', 'contents': [
                     {'type': 'text', 'text': '⏰ Hạn đăng ký', 'align': 'center', 'color': GREEN, 'size': 'md'},
                     {'type': 'text', 'text': f'{days[deadline.weekday()]} · {deadline:%H:%M}',
                      'weight': 'bold', 'align': 'center', 'size': 'xl', 'color': '#20342B'},
                     {'type': 'text', 'text': f'{deadline:%d/%m/%Y} · Giờ Nhật', 'align': 'center',
                      'size': 'xs', 'color': '#52645A'}]})
    body[1]['align'] = 'center'
    body[1]['size'] = 'lg'
    result['contents']['footer']['contents'][0]['action']['label'] = 'ĐĂNG KÝ NGAY →'
    return result


def welcome_card(signup_url):
    return card('Chào mừng bạn! Đăng ký kibo và xác nhận tên.', 'Chào mừng bạn!',
                'Chọn đúng tên của mình một lần để quán nhận diện khi đăng ký lịch rảnh. '
                'Những lần sau hệ thống sẽ tự nhớ tên.', signup_url)
