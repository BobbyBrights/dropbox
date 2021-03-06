import asyncio
import os, re, uuid, hashlib, datetime, json
from . import toolbox, oss, mask, fs
from aiohttp import ClientSession
from aiohttp_session import get_session

@asyncio.coroutine
def route(request):

    session = yield from get_session(request)
    if 'uid' in session:
        uid = session['uid']
    else:
        return toolbox.javaify(403,"forbidden")

    if request.content_type != "multipart/form-data":
        return toolbox.javaify(400,"content type error")

    query_parameters = request.rel_url.query
    if "dir" in query_parameters:
        directory = query_parameters["dir"]
    else:
        return toolbox.javaify(400,"miss parameter")

    with (yield from request.app['pool']) as connect:
        cursor = yield from connect.cursor()
        
        directory_id = yield from fs.directory_exists(cursor,uid,directory)

        if not directory_id:
            yield from cursor.close()
            connect.close()
            return toolbox.javaify(400,"target error")

        try:
            reader = yield from request.multipart()
            part = yield from reader.next()
        except Exception as e:
            print(e)
            yield from cursor.close()
            connect.close()
            return toolbox.javaify(400,"bad request")

        if part is None:
            yield from cursor.close()
            connect.close()
            return toolbox.javaify(400,"bad request")

        try:
            file_name = re.search(r'filename="([^"]+)"',part.headers["Content-Disposition"]).group(1)
            file_extension = os.path.splitext(file_name)[-1][1:]
        except:
            yield from cursor.close()
            connect.close()
            return toolbox.javaify(400,"bad request")

        if fs.name_illegal(file_name):
            return toolbox.javaify(400,"name illegal")

        file_meta = yield from fs.file_query(cursor,directory_id,[file_name])

        if file_meta and file_meta[0]["type"] == "directory" and file_meta[0]["status"] == 1:
            yield from cursor.close()
            connect.close()
            return toolbox.javaify(400,"directory exists")

        now = datetime.datetime.now()
        
        # size = 0
        # temp_path = os.path.join(request.app["temp_dir"],str(uuid.uuid4()))
        # f = open(temp_path,'wb')
        # md5 = hashlib.md5()
        
        # while True:

        #     try:
        #         chunk = yield from part.read_chunk()  # 8192 bytes by default
        #     except:
        #         yield from cursor.close()
        #         connect.close()
        #         return toolbox.javaify(415,"unsupported media type")

        #     if not chunk:
        #         md5 = md5.hexdigest()
        #         f.close()
        #         file_type = fs.mime_detect(temp_path)
        #         break
        #     else:
        #         size += len(chunk)
        #         f.write(chunk)
        #         md5.update(chunk)

        session = ClientSession()
        boundary = '----------{}'.format(uuid.uuid4().hex)
        content_type = 'multipart/form-data; boundary={}'.format(boundary)
        try:
            response = yield from session.post('http://up-z2.qiniu.com',data=rewrite(uid,part,boundary),headers={'Content-Type':content_type})
            json_back = yield from response.text()
            yield from session.close()
            json_back = json.loads(json_back)
            file_type = json_back["type"]
            size = json_back["size"]
            md5 = json_back["key"].split("/")[-1]
        except Exception as error:
            print(error)
            yield from cursor.close()
            connect.close()
            return toolbox.javaify(500,"something wrong")


        if file_meta and file_meta[0]["type"] == "directory" and file_meta[0]["status"] == 0:
            
            yield from fs.file_delete(cursor,[file_meta[0]["id"]])
            request.app.loop.create_task(fs.file_delete_async(request.app['pool'],[file_meta[0]["id"]]))
            file_meta = None

        if not file_meta:
            
            file_id = yield from fs.file_create(cursor,{
                "directory": directory_id,
                "name": file_name,
                "type": file_type,
                "modify": toolbox.time_str(now),
                "size": size,
                "md5": md5,
            })

        else:

            file_id = file_meta[0]["id"]
            yield from fs.file_modify(cursor,file_id,{
                "type": file_type,
                "modify": toolbox.time_str(now),
                "size": size,
                "md5": md5,
                "status": 1
            })

        # yield from fs.version_sync(cursor,file_id)

        yield from fs.file_modify(cursor,directory_id,{
            "modify": toolbox.time_str(now)
        })

        # if not file_meta or md5 != file_meta[0]['md5']:
        #     request.app.loop.create_task(oss.transmit(temp_path,"{}/{}".format(str(uid).zfill(8),md5)))
        #     pass
        
        yield from connect.commit()
        yield from cursor.close()
        connect.close()

        return toolbox.javaify(200,"success",{
            "name": file_name,
            "type": file_type,
            "extension": file_extension,
            "modify": toolbox.time_utc(now),
            "owner": "self",
            "size": size,
            "source": mask.generate(uid,md5,file_extension)
        })


# python3.6 only
async def rewrite(uid,part,boundary):
    CRLF = '\r\n'
    content_disposition = 'Content-Disposition: form-data; name="file"; filename="file"'
    content_type = 'Content-Type: {}'.format(part.headers['Content-Type'])
    yield CRLF.join(['--' + boundary,content_disposition,content_type,'','']).encode()
    md5 = hashlib.md5()

    while True:
        chunk = await part.read_chunk()
        if not chunk:
            break
        md5.update(chunk)
        yield chunk

    key = '{}/{}'.format(str(uid).zfill(8),md5.hexdigest())
    token = oss.generate_token(key)
    yield CRLF.join(['','--'+boundary,'Content-Disposition: form-data; name="key"','',key]).encode()
    yield CRLF.join(['','--'+boundary,'Content-Disposition: form-data; name="token"','',token]).encode()
    yield CRLF.join(['','--'+boundary+'--','']).encode()