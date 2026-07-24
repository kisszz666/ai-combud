"""
百度贴吧 Selenium 爬虫 - 绕过反爬验证的备用方案
================================================
特性：
  - 使用 Selenium 启动真实浏览器，绕过百度安全验证
  - 支持半自动模式：用户可手动通过验证码
  - 支持手动输入 BDUSS Cookie（无需登录界面）
  - 与 tieba_scraper.py 共享数据格式和输出目录

使用方法：
  1. 手动模式（推荐首次使用）：
     python tieba_selenium.py --kw 电脑 --limit 20 --manual
     然后在打开的浏览器中手动登录、通过验证码，完成后在终端按回车

  2. Cookie 模式（如果有 BDUSS）：
     python tieba_selenium.py --kw 电脑 --limit 20 --bduss "你的BDUSS"

  3. 完全自动模式（可能会被验证）：
     python tieba_selenium.py --kw 电脑 --limit 20

依赖：
  pip install selenium
  另外需要安装 Chrome 或 Edge 浏览器
"""

import os
import sys
import time
import json
import csv
import argparse
import urllib.parse
from html import unescape

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
except ImportError:
    sys.exit("缺少 selenium 库，请先安装: pip install selenium")

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("缺少 beautifulsoup4 库，请先安装: pip install beautifulsoup4")


# 配置
DEFAULT_TIMEOUT = 30
PAGE_WAIT = 2
SCROLL_PAUSE = 1.5


def create_driver(headless=False):
    """创建 Selenium WebDriver，尝试 Chrome 然后 Edge。"""
    
    # 尝试 Chrome
    try:
        options = ChromeOptions()
        if headless:
            options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-blink-features=AutomationControlled')
        
        # 移除自动化特征
        options.add_experimental_option('excludeSwitches', ['enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)
        
        driver = webdriver.Chrome(options=options)
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            '''
        })
        print("[OK] Chrome 浏览器启动成功")
        return driver
    except Exception as e:
        print(f"[INFO] Chrome 启动失败: {e}")
    
    # 尝试 Edge
    try:
        options = EdgeOptions()
        if headless:
            options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
        driver = webdriver.Edge(options=options)
        print("[OK] Edge 浏览器启动成功")
        return driver
    except Exception as e:
        print(f"[ERROR] Edge 也启动失败: {e}")
        return None


def wait_for_manual_login(driver, timeout=120):
    """等待用户手动登录并通过验证。"""
    print("\n" + "="*60)
    print("请在浏览器中完成以下操作：")
    print("  1. 如果有验证码，请手动通过")
    print("  2. 如果需要登录，请先登录")
    print("  3. 完成后回到终端，按回车键继续...")
    print("="*60)
    
    start_time = time.time()
    input("\n>>> 完成后请按回车键开始爬取...")
    
    elapsed = time.time() - start_time
    print(f"\n[INFO] 用户准备完成（等待时间: {elapsed:.1f}秒）")
    return True


def get_cookies_as_dict(driver):
    """从浏览器获取 cookies 字典。"""
    cookies = driver.get_cookies()
    return {cookie['name']: cookie['value'] for cookie in cookies}


def scroll_to_load_all(driver, max_scrolls=5):
    """滚动页面以加载所有内容。"""
    for i in range(max_scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(SCROLL_PAUSE)


def extract_list_from_page(driver):
    """从当前页面提取帖子列表。"""
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    posts = []
    
    # 尝试多个选择器
    selectors = [
        '.threadlist_lz .threadlist_title a',  # 旧版列表
        '.t_con .title a',                      # 新版列表
        '.threadlist_abs a',                    # 另一个选择器
        '.left_section a.title',                # 新UI
        'a[href*="/p/"]',                       # 通用匹配
    ]
    
    elements = []
    for selector in selectors:
        elements = soup.select(selector)
        if elements:
            print(f"[INFO] 使用选择器 '{selector}' 找到 {len(elements)} 个元素")
            break
    
    # 如果以上都没找到，尝试通用方法
    if not elements:
        print("[INFO] 尝试通用匹配...")
        all_links = soup.find_all('a', href=True)
        print(f"[DEBUG] 页面共有 {len(all_links)} 个链接")
        for link in all_links[:5]:
            print(f"[DEBUG] 链接示例: {link.get('href', '')}")
        elements = [a for a in all_links if '/p/' in a.get('href', '')]
    
    for el in elements:
        href = el.get('href', '')
        if '/p/' in href:
            tid = href.split('/p/')[-1].split('?')[0]
            title = el.get_text(strip=True)
            # 过滤掉太短或重复的标题
            if tid and title and len(title) >= 2:
                posts.append({
                    'tid': tid,
                    'title': title,
                    'url': f'https://tieba.baidu.com/p/{tid}',
                })
    
    print(f"[INFO] 最终提取到 {len(posts)} 个帖子")
    return posts


def extract_detail_from_page(driver):
    """从当前详情页提取帖子详情。"""
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    
    detail = {
        'author': '',
        'post_time': '',
        'content': '',
        'images': [],
        'replies': [],
    }
    
    # 提取作者
    author_selectors = ['.p_author_name', '.user_name', '.b_user_name']
    for sel in author_selectors:
        el = soup.select_one(sel)
        if el:
            detail['author'] = el.get_text(strip=True)
            break
    
    # 提取发布时间
    time_selectors = ['.p_posttime', '.post_time', '.time']
    for sel in time_selectors:
        el = soup.select_one(sel)
        if el:
            detail['post_time'] = el.get_text(strip=True)
            break
    
    # 提取内容（楼主主贴）
    content_selectors = ['.dede_content', '.p_content', '.d_post_content']
    for sel in content_selectors:
        el = soup.select_one(sel)
        if el:
            detail['content'] = el.get_text('\n', strip=True)
            break
    
    # 提取图片
    img_selectors = ['.d_post_content img', '.content img', 'img.bigpic']
    for sel in img_selectors:
        imgs = soup.select(sel)
        for img in imgs:
            src = img.get('src', '') or img.get('data-src', '')
            if src and ('tieba' in src or 'baidu' in src):
                if src not in detail['images']:
                    detail['images'].append(src)
    
    # 提取回复
    reply_selectors = ['.l_post', '.reply_list li']
    for sel in reply_selectors:
        replies = soup.select(sel)
        for reply in replies[1:]:  # 跳过第一个（楼主）
            reply_author = ''
            reply_content = ''
            
            # 回复作者
            ra = reply.select_one('.p_author_name, .user_name')
            if ra:
                reply_author = ra.get_text(strip=True)
            
            # 回复内容
            rc = reply.select_one('.d_post_content, .reply_content')
            if rc:
                reply_content = rc.get_text('\n', strip=True)
            
            if reply_author and reply_content:
                detail['replies'].append({
                    'author': reply_author,
                    'content': reply_content,
                })
    
    return detail


def run_selenium_scraper(kw="电脑", limit=20, out_dir=None, 
                         bduss=None, manual_mode=False, headless=False,
                         progress_callback=None):
    """
    Selenium 爬虫主函数。
    
    参数:
        kw: 贴吧名称
        limit: 目标帖子数
        out_dir: 输出目录
        bduss: BDUSS cookie（可选）
        manual_mode: 手动模式（用户手动通过验证）
        headless: 是否无头模式
        progress_callback: 进度回调函数
    
    返回:
        (results list, status dict)
    """
    
    def _log(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)
    
    status = {'ok': False, 'message': '', 'total': 0}
    
    # 初始化驱动
    _log("[STEP 1] 初始化浏览器...")
    driver = create_driver(headless=headless)
    if not driver:
        status['message'] = '无法启动浏览器'
        return [], status
    
    try:
        # 设置窗口大小
        driver.set_window_size(1280, 800)
        
        # 导航到贴吧
        kw_enc = urllib.parse.quote(kw)
        list_url = f"https://tieba.baidu.com/f?kw={kw_enc}&ie=utf-8"
        
        _log(f"[STEP 2] 访问贴吧: {kw}")
        driver.get(list_url)
        
        # 等待页面加载完成
        _log("[STEP 2.1] 等待页面加载...")
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/p/'], .threadlist_lz, .t_con"))
            )
            _log("[STEP 2.2] 页面加载完成")
        except TimeoutException:
            _log("[STEP 2.2] 等待超时，继续尝试...")
        time.sleep(2)
        
        # 如果有 BDUSS，添加 cookie 并重新加载
        if bduss:
            _log("[STEP 2.3] 添加 BDUSS Cookie...")
            driver.add_cookie({
                'name': 'BDUSS',
                'value': bduss,
                'domain': '.baidu.com',
                'path': '/',
            })
            driver.refresh()
            time.sleep(PAGE_WAIT)
        
        # 手动模式：等待用户通过验证
        if manual_mode:
            wait_for_manual_login(driver)
            _log("[STEP 2.2] 用户准备完成，重新加载页面...")
            driver.refresh()
            time.sleep(PAGE_WAIT)
        
        # 滚动加载
        _log("[STEP 3] 加载页面内容...")
        scroll_to_load_all(driver, max_scrolls=3)
        
        # 提取列表
        _log("[STEP 4] 提取帖子列表...")
        all_posts = []
        seen_tids = set()
        
        current_page = 1
        while len(all_posts) < limit and current_page <= 10:  # 最多翻10页
            posts = extract_list_from_page(driver)
            new_posts = [p for p in posts if p['tid'] not in seen_tids]
            
            for p in new_posts:
                seen_tids.add(p['tid'])
                all_posts.append(p)
                if len(all_posts) >= limit:
                    break
            
            _log(f"  第 {current_page} 页: 发现 {len(posts)} 个帖子，累计 {len(all_posts)} 个")
            
            if len(all_posts) >= limit:
                break
            
            # 点击下一页
            try:
                next_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, 'a.next, a[class*="next"]'))
                )
                next_btn.click()
                time.sleep(PAGE_WAIT)
                scroll_to_load_all(driver, max_scrolls=2)
                current_page += 1
            except TimeoutException:
                _log("[INFO] 没有更多页面")
                break
        
        if not all_posts:
            status['message'] = '未能提取到任何帖子'
            _log("[ERROR] 未能提取到任何帖子")
            return [], status
        
        _log(f"[STEP 5] 开始爬取 {len(all_posts)} 个帖子详情...")
        
        # 创建输出目录
        if out_dir is None:
            out_dir = os.path.join(os.getcwd(), f"tieba_{kw}", "output")
        os.makedirs(out_dir, exist_ok=True)
        
        # 爬取详情
        results = []
        security_detected = False
        full_detail_count = 0
        empty_detail_count = 0  # 连续空内容计数
        
        for i, post in enumerate(all_posts[:limit], 1):
            _log(f"  [{i}/{min(len(all_posts), limit)}] 《{post['title'][:30]}》")
            
            try:
                # 如果之前已检测到安全验证或连续空内容，跳过详情页
                if security_detected or (empty_detail_count >= 2 and not manual_mode):
                    if not security_detected and empty_detail_count >= 2:
                        _log("  [检测] 连续空内容，停止尝试详情页")
                        security_detected = True  # 设置标志
                    _log("  [跳过] 使用列表页基本信息")
                    results.append({
                        'index': i,
                        'tid': post['tid'],
                        'title': post['title'],
                        'author': '',
                        'post_time': '',
                        'like_count': 0,
                        'reply_count': 0,
                        'list_date': '',
                        'content': '',
                        'image_count': 0,
                        'images': [],
                        'local_images': [],
                        'replies': [],
                    })
                    continue
                
                driver.get(post['url'])
                time.sleep(PAGE_WAIT)
                
                # 检查是否触发安全验证（多种检测方式）
                page_title = driver.title
                page_source_start = driver.page_source[:2000]
                
                is_security = (
                    "安全验证" in page_title or
                    "bioc" in page_source_start.lower() or
                    "百度安全验证" in page_title or
                    "security-check" in page_source_start.lower() or
                    "captcha" in page_source_start.lower()[:500]
                )
                
                if is_security:
                    _log(f"  [⚠️] 触发安全验证！标题: {page_title}")
                    security_detected = True
                    empty_detail_count += 1
                    
                    # 只有手动模式才允许用户通过验证
                    if manual_mode and i == 1:
                        _log("  [提示] 请在浏览器中手动通过验证，完成后按回车继续...")
                        try:
                            input("  >>> 完成验证后按回车继续...")
                            driver.refresh()
                            time.sleep(PAGE_WAIT)
                            security_detected = False  # 重置标志
                        except EOFError:
                            _log("  [警告] 无法获取用户输入，跳过详情页")
                            results.append({
                                'index': i,
                                'tid': post['tid'],
                                'title': post['title'],
                                'author': '',
                                'post_time': '',
                                'like_count': 0,
                                'reply_count': 0,
                                'list_date': '',
                                'content': '',
                                'image_count': 0,
                                'images': [],
                                'local_images': [],
                                'replies': [],
                            })
                            continue
                    else:
                        # 非手动模式，直接使用列表信息
                        _log("  [跳过] 使用列表页基本信息")
                        results.append({
                            'index': i,
                            'tid': post['tid'],
                            'title': post['title'],
                            'author': '',
                            'post_time': '',
                            'like_count': 0,
                            'reply_count': 0,
                            'list_date': '',
                            'content': '',
                            'image_count': 0,
                            'images': [],
                            'local_images': [],
                            'replies': [],
                        })
                        continue
                
                scroll_to_load_all(driver, max_scrolls=2)
                
                detail = extract_detail_from_page(driver)
                
                # 检测是否获取到了有效内容
                has_content = detail['content'] or detail['author'] or len(detail['replies']) > 0
                
                if not has_content:
                    empty_detail_count += 1
                    _log(f"  [警告] 未获取到详情内容 (连续 {empty_detail_count} 次)")
                    results.append({
                        'index': i,
                        'tid': post['tid'],
                        'title': post['title'],
                        'author': '',
                        'post_time': '',
                        'like_count': 0,
                        'reply_count': 0,
                        'list_date': '',
                        'content': '',
                        'image_count': 0,
                        'images': [],
                        'local_images': [],
                        'replies': [],
                    })
                    continue
                
                # 获取到有效内容，重置计数器
                empty_detail_count = 0
                full_detail_count += 1
                
                results.append({
                    'index': i,
                    'tid': post['tid'],
                    'title': post['title'],
                    'author': detail['author'],
                    'post_time': detail['post_time'],
                    'like_count': 0,
                    'reply_count': len(detail['replies']),
                    'list_date': '',
                    'content': detail['content'],
                    'image_count': len(detail['images']),
                    'images': detail['images'],
                    'local_images': [],
                    'replies': detail['replies'],
                })
                
            except Exception as e:
                _log(f"  [WARN] 爬取失败: {e}")
                continue
        
        if results:
            # 保存数据
            _log("[STEP 6] 保存数据...")
            
            csv_path = os.path.join(out_dir, 'posts.csv')
            json_path = os.path.join(out_dir, 'posts.json')
            
            with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
                cols = ['index', 'tid', 'title', 'author', 'post_time', 'like_count',
                        'reply_count', 'list_date', 'image_count', 'images', 'content']
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                for rec in results:
                    row = dict(rec)
                    row.pop('replies', None)
                    row.pop('local_images', None)
                    row['images'] = ';'.join(rec.get('images', []))
                    w.writerow(row)
            
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            
            total = len(results)
            partial_count = total - full_detail_count
            if partial_count > 0:
                _log(f"[OK] 成功保存 {total} 个帖子（{full_detail_count} 完整 + {partial_count} 仅标题）")
                status['message'] = f'成功 {total} 帖（{full_detail_count} 完整，{partial_count} 仅标题）'
                status['partial'] = True
            else:
                _log(f"[OK] 成功保存 {total} 个完整帖子")
                status['message'] = f'成功获取 {total} 个完整帖子'
            
            status['ok'] = True
            status['total'] = total
            status['full_count'] = full_detail_count
            status['partial_count'] = partial_count
        
        return results, status
        
    except KeyboardInterrupt:
        _log("\n[INFO] 用户中断")
        return [], {'ok': False, 'message': '用户中断'}
    finally:
        try:
            driver.quit()
            _log("[INFO] 浏览器已关闭")
        except:
            pass


def main():
    ap = argparse.ArgumentParser(description='百度贴吧 Selenium 爬虫（绕过反爬验证）')
    ap.add_argument('--kw', default='电脑', help='贴吧名称')
    ap.add_argument('--limit', type=int, default=20, help='抓取帖子数量')
    ap.add_argument('--bduss', default=None, help='BDUSS Cookie（可选）')
    ap.add_argument('--out', default=None, help='输出目录')
    ap.add_argument('--manual', action='store_true', help='手动模式（推荐，可通过验证码）')
    ap.add_argument('--headless', action='store_true', help='无头模式（可能被检测）')
    args = ap.parse_args()
    
    print("="*60)
    print("百度贴吧 Selenium 爬虫")
    print("="*60)
    print(f"贴吧: {args.kw}")
    print(f"数量: {args.limit}")
    print(f"模式: {'手动' if args.manual else '自动'}")
    print("="*60 + "\n")
    
    results, status = run_selenium_scraper(
        kw=args.kw,
        limit=args.limit,
        out_dir=args.out,
        bduss=args.bduss,
        manual_mode=args.manual,
        headless=args.headless,
    )
    
    if status['ok']:
        print(f"\n[SUCCESS] {status['message']}")
    else:
        print(f"\n[FAILED] {status['message']}")
        sys.exit(1)


if __name__ == '__main__':
    main()