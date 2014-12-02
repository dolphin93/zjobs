
import os
from os.path import dirname, realpath
import sys
import time
app_home_dir = dirname(dirname(realpath(__file__)))
sys.path.append(app_home_dir)  ### setup sys path to use the current app modules

import app.config as config
import pg8000 as dbi
from app.config import logger
from jobcrawler.items import JobItem

from multiprocessing import Pool

def create_db():
    #conn = sqlite3.connect(config.DB_FILE)
    conn = dbi.connect(host=config.DB_HOST, database=config.DATABASE, user=config.DB_USER, password=config.DB_PASSWORD)
    #conn = dbi.connect('postgres://zjobs:zjobs@localhost:5432/zjobs')
    try:
        c = conn.cursor()

        c.execute('DROP TABLE IF EXISTS CRAWLED_JOBS')
        c.execute('DROP INDEX IF EXISTS job_title_idx')

        logger.info("dropped related tables and indexes")

        # c.execute('''
        #     CREATE TABLE IF NOT EXISTS CRAWLED_JOBS(
        #         source            text,
        #         crawled_date      timestamp,
        #         publish_date      timestamp,
        #         job_title         text,
        #         job_desc          text,
        #         job_details_link  text,
        #         job_location      text,
        #         job_country       text,
        #         salary            text,
        #         employer_name     text,
        #         contact           text
        #     );
        # ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS CRAWLED_JOBS(
                source            text,
                crawled_date      date,
                publish_date      date,
                job_title         text,
                job_desc          text,
                job_details_link  text,
                job_location      text,
                job_country       text,
                salary            text,
                employer_name     text,
                contact           text
            );
            ''')

        logger.info("created related tables")

        c.execute('''
            CREATE UNIQUE INDEX job_title_idx ON CRAWLED_JOBS(job_title)
        ''')

        logger.info("created related indexes")

        conn.commit()
        logger.info('done create database')
    except:
        conn.rollback()
        logger.error('Unable to run create_db')
    finally:
        conn.close()
    
def _crawl(spider_name=None):
        if spider_name:
            os.system('cd %s && scrapy crawl %s' % (app_home_dir, spider_name))
            logger.info('Done running spider %s' % spider_name)
        return None

def run_crawler():
    start_time = time.time()
    logger.info('start running crawler..')

    # os.system('python '+ app_home_dir +'/app/run_crawler.py')
    spider_names = ['sgxin', 'shichengbbs', 'singxin']
    
    pool = Pool(processes=len(spider_names))
    pool.map(_crawl, spider_names)
   
    logger.info('done running crawler.. Time elapsed: %.3fs' % (time.time() - start_time))

def run_web():
    logger.info('starting web..')
    os.system('cd '+ app_home_dir +' && gunicorn -c app/gunicorn.conf.py web.jobboard:app --debug')

def run_flask_web():
    import web.jobboard
    web.jobboard.app.run(host='0.0.0.0', port=config.WEB_HTTP_PORT, debug=config.WEB_DEBUG_ENABLED)

def run_heartbeater():
    import urllib

    logger.info('scheduler started heartbeating..')
    resp = urllib.urlopen(config.APP_HEARTBEAT_URL)
    resp.read()
    logger.info('scheduler done hearting beating')

def run_housekeeper():

    logger.info('start running housekeeper..')
    JobItem.remove_old_records(retention_days=config.HOUSEKEEPING_RECORD_ORDLER_THAN)
    logger.info('done running housekeeper..')

def extract_file_as_bytes(format='xlsx'):
    import xlsxwriter
    import unicodecsv
    import tempfile

    tmp_file = (tempfile.NamedTemporaryFile(prefix='zjobs.', suffix=('.%s' % format), delete=False)).name      
    
    property_names, rows = JobItem.findall()
    if format.lower() == 'xlsx':
        workbook = xlsxwriter.Workbook(tmp_file, {'default_date_format':'yyyy-mm-dd'})
        worksheet = workbook.add_worksheet('crawled_jobs')
        worksheet.set_column('A:M', 40)

        worksheet.write_row(0, 0, [property_name.upper() for property_name in property_names])

        for rowIdx, row in enumerate(rows):
            worksheet.write_row(rowIdx+1, 0, row)
        
        workbook.close()
    elif format.lower() == 'csv':
        with open(tmp_file, 'w') as f:
            writer = unicodecsv.writer(f, encoding='utf-8')
            writer.writerow([property_name.upper() for property_name in property_names])
            for row in rows:
                writer.writerow(row)
    else:
        os.remove(tmp_file)
        raise Exception("'%s' format is not supported" % format)
    
    file_content = open(tmp_file, 'rb').read()
    os.remove(tmp_file)
    return file_content


def run_emailer():
    from email.mime.base import MIMEBase
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email import Encoders
    import smtplib

    def send_results(spider_names):
        smtp = smtplib.SMTP(host=config.SMTP_HOST, port=config.SMTP_PORT)
        smtp.set_debuglevel(4)
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(user=config.SMTP_USER, password=config.SMTP_PASSWORD)

        toaddrs = config.TO_ADDRS
        fromaddr = config.FROM_ADDR

        for spider_name in spider_names:
            message_subject = "Crawled Result From %s" % spider_name
            message_text = "Please find the crawled result for %s" % spider_name
            file_to_attach = config.APP_HOME + '/crawled_jobs_%s.csv' % spider_name

            msg = MIMEMultipart()
            msg['From'] = fromaddr
            msg['To'] = ','.join(toaddrs)
            msg['Subject'] = message_subject
            msg.attach(MIMEText(message_text))

            part = MIMEBase('application', "octet-stream")
            part.set_payload(open(file_to_attach, "rb").read())
            Encoders.encode_base64(part)
            part.add_header('Content-Disposition', 'attachment; filename="%s"' % os.path.basename(file_to_attach))
            msg.attach(part)

            smtp.sendmail(fromaddr, toaddrs, msg.as_string())

        smtp.quit()



def run_scheduler():
    from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.schedulers.background import BackgroundScheduler

    executors = {
        'default': ThreadPoolExecutor(2),
        'processpool': ProcessPoolExecutor(2)
    }
    job_defaults = {
        'coalesce': False,
        'max_instances': 3
    }
    scheduler = BackgroundScheduler(executors=executors, job_defaults=job_defaults)
    crawler_trigger = CronTrigger(hour='*/08')
    #crawler_trigger = CronTrigger(minute='*/05')
    hourse_keeping_trigger = CronTrigger(hour='12', minute='30')

    heartbeat_trigger = CronTrigger(minute='*/30')

    scheduler.add_job(func=run_crawler, trigger=crawler_trigger)
    scheduler.add_job(func=run_housekeeper, trigger=hourse_keeping_trigger)
    scheduler.add_job(func=run_heartbeater, trigger=heartbeat_trigger)
    
    scheduler.start()
    logger.info('scheduler is started')


def run_app():
    run_scheduler()
    run_web()

def parse_process_args():
    import argparse
    parser = argparse.ArgumentParser('run the app component')
    parser.add_argument('component', nargs='?', default='all', type=str,  help='app component to run. [all|web|flask_web|scheduler|crawler|housekeeper|heartbeater]')
    args = parser.parse_args()

    if args.component is None:
        run_app()
    elif args.component == 'all':
        run_app()
    elif args.component == 'scheduler':
        run_scheduler()
    elif args.component == 'crawler':
        run_crawler()
    elif args.component == 'housekeeper':
        run_housekeeper()
    elif args.component == 'heartbeater':
        run_heartbeater()
    elif args.component == 'web':
        run_web()
    elif args.component == 'flask_web':
        run_flask_web()
    elif args.component == 'create_db':
        create_db()
    else:
        print 'Invalid Usage: '
        parser.print_help()

if __name__ == '__main__':
    parse_process_args()