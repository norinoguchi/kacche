import json
import os
import boto3
from botocore.exceptions import ClientError

s3 = boto3.client('s3')
BUCKET_NAME = os.environ.get('S3_BUCKET_NAME')

def lambda_handler(event, context):
    http_method = event.get('requestContext', {}).get('http', {}).get('method', 'GET')
    raw_path = event.get('rawPath', '')
    
    # パスからIDやアクションを抽出
    parts = [p for p in raw_path.split('/') if p]
    
    event_id = None
    is_answers = False
    
    if 'events' in parts:
        idx = parts.index('events')
        if len(parts) > idx + 1:
            event_id = parts[idx + 1]
        if 'answers' in parts:
            is_answers = True

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT,DELETE"
    }
    
    if http_method == 'OPTIONS':
        return {"statusCode": 200, "headers": headers, "body": ""}

    try:
        # 1. イベント新規作成 (POST /events)
        if http_method == 'POST' and not event_id:
            body = json.loads(event.get('body', '{}'))
            new_event_id = 'evt-' + os.urandom(4).hex()
            
            event_data = {
                "id": new_event_id,
                "name": body.get('name'),
                "desc": body.get('desc', {}),
                "location": body.get('location', {}), # <-- ★ここに location を追加
                "deadline": body.get('deadline'),
                "organizerEmail": body.get('organizerEmail'),
                "candidates": body.get('candidates', []),
                "answers": []
            }
            
            s3.put_object(
                Bucket=BUCKET_NAME,
                Key=f"events/{new_event_id}.json",
                Body=json.dumps(event_data, ensure_ascii=False),
                ContentType='application/json'
            )
            
            return {
                "statusCode": 201,
                "headers": headers,
                "body": json.dumps({"message": "Success", "event": event_data}, ensure_ascii=False)
            }

        # 2. イベント一覧取得 (GET /events)
        if http_method == 'GET' and not event_id:
            try:
                response = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix='events/')
                events_list = []
                
                if 'Contents' in response:
                    for obj in response['Contents']:
                        key = obj['Key']
                        if key.endswith('.json'):
                            obj_res = s3.get_object(Bucket=BUCKET_NAME, Key=key)
                            ev_data = json.loads(obj_res['Body'].read().decode('utf-8'))
                            events_list.append({
                                "id": ev_data.get("id"),
                                "name": ev_data.get("name"),
                                "deadline": ev_data.get("deadline"),
                                "organizerEmail": ev_data.get("organizerEmail")
                            })
                
                return {
                    "statusCode": 200,
                    "headers": headers,
                    "body": json.dumps(events_list, ensure_ascii=False)
                }
            except Exception as e:
                return {"statusCode": 500, "headers": headers, "body": json.dumps({"error": str(e)}, ensure_ascii=False)}

        # 3. イベント詳細取得 (GET /events/{eventId})
        if http_method == 'GET' and event_id and not is_answers:
            try:
                response = s3.get_object(Bucket=BUCKET_NAME, Key=f"events/{event_id}.json")
                event_data = json.loads(response['Body'].read().decode('utf-8'))
                return {
                    "statusCode": 200,
                    "headers": headers,
                    "body": json.dumps(event_data, ensure_ascii=False)
                }
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchKey':
                    return {"statusCode": 404, "headers": headers, "body": json.dumps({"error": "Event not found"}, ensure_ascii=False)}
                raise e

        # 3-2. イベント情報・設定の全体更新 (PUT /events/{eventId}) <-- ★追加しました
        if http_method == 'PUT' and event_id and not is_answers:
            body = json.loads(event.get('body', '{}'))
            file_key = f"events/{event_id}.json"
            
            try:
                # 既存データを取得して存在確認
                response = s3.get_object(Bucket=BUCKET_NAME, Key=file_key)
                existing_data = json.loads(response['Body'].read().decode('utf-8'))
            except ClientError:
                return {"statusCode": 404, "headers": headers, "body": json.dumps({"error": "Event not found"}, ensure_ascii=False)}

            # 送信された新しいデータで上書き（answersは既存のものを維持しつつ更新）
            updated_event = {
                "id": event_id,
                "name": body.get('name', existing_data.get('name')),
                "desc": body.get('desc', existing_data.get('desc', {})),
                "location": body.get('location', existing_data.get('location', {})), # <-- ★場所データを確実に保存
                "deadline": body.get('deadline', existing_data.get('deadline')),
                "organizerEmail": body.get('organizerEmail', existing_data.get('organizerEmail')),
                "candidates": body.get('candidates', existing_data.get('candidates', [])),
                "answers": body.get('answers', existing_data.get('answers', []))
            }

            s3.put_object(
                Bucket=BUCKET_NAME,
                Key=file_key,
                Body=json.dumps(updated_event, ensure_ascii=False),
                ContentType='application/json'
            )

            return {
                "statusCode": 200,
                "headers": headers,
                "body": json.dumps({"message": "Event updated", "event": updated_event}, ensure_ascii=False)
            }

        # 4. 回答送信・更新 (POST /events/{eventId}/answers)
        if http_method == 'POST' and event_id and is_answers:
            body = json.loads(event.get('body', '{}'))
            nickname = body.get('nickname')
            
            if not nickname:
                return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "Nickname required"}, ensure_ascii=False)}

            file_key = f"events/{event_id}.json"
            try:
                response = s3.get_object(Bucket=BUCKET_NAME, Key=file_key)
                event_data = json.loads(response['Body'].read().decode('utf-8'))
            except ClientError:
                return {"statusCode": 404, "headers": headers, "body": json.dumps({"error": "Event not found"}, ensure_ascii=False)}

            answers = event_data.get('answers', [])
            existing_index = next((i for i, a in enumerate(answers) if a.get('nickname') == nickname), -1)
            
            new_answer = {
                "nickname": nickname,
                "fullname": body.get('fullname'),
                "email": body.get('email'),
                "availability": body.get('availability', {})
            }
            
            if existing_index >= 0:
                answers[existing_index] = new_answer
            else:
                answers.append(new_answer)
                
            event_data['answers'] = answers

            s3.put_object(
                Bucket=BUCKET_NAME,
                Key=file_key,
                Body=json.dumps(event_data, ensure_ascii=False),
                ContentType='application/json'
            )

            return {
                "statusCode": 200,
                "headers": headers,
                "body": json.dumps({"message": "Answer saved", "event": event_data}, ensure_ascii=False)
            }

        return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "Invalid request path"}, ensure_ascii=False)}

    except Exception as e:
        return {"statusCode": 500, "headers": headers, "body": json.dumps({"error": str(e)}, ensure_ascii=False)}