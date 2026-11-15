# إعادة تنفيذ تجارب القراءة

تستخدم الجلسة الحالية `/workspace/SmartGov-IDA` وبيئة Python 3.12 في `/workspace/smartgov-venv`. كل مهمة سحابية معزولة بالفعل؛ استخدم checkout الموجود ولا تنشئ Git worktree دون طلب المستخدم.

## التجهيز المتصل بالشبكة

```bash
cd /workspace/SmartGov-IDA
python -m venv /workspace/smartgov-venv
/workspace/smartgov-venv/bin/pip install -r requirements-dev.lock
/workspace/smartgov-venv/bin/python scripts/fetch_tesseract_models.py --output /workspace/smartgov-models/tesseract
/workspace/smartgov-venv/bin/python scripts/generate_reading_corpus.py --output /workspace/smartgov-corpus/v1
```

يلزم وجود خطوط Noto Naskh Arabic وNoto Sans Arabic وDejaVu Sans في المسارات المسجلة داخل مولد العينة. لا تُنزّل الخطوط ضمن المعالجة. في جهاز مختلف جهزها وإشعاراتها قبل العمل؛ مولد البيانات خاص ببيئة Linux الحالية، لا ضمان لتطابق ملفات PDF عبر منصات مختلفة.

التنزيل الصريح لبيانات Tesseract مثبت بمراجعة مصدر وبصمة لكل ملف، ويقبل الملفات الصحيحة الموجودة ولا يستبدل ملفات غير مطابقة بصمتها. المكتبة المثبتة tesserocr تضم Tesseract 5.5.1؛ أمر النظام tesseract 5.5.0 ليس المحرك المستخدم في هذه التجربة. لا تخلط نتائجهما.

للاستعداد لمقارنة Paddle فقط، استخدم `requirements-comparison.lock` في بيئة التجربة؛ لا تحتاجه لاستخدام القارئ الحالي. استدلال Paddle غير مجهز بعد بسبب حجب الأوزان، فلا تعد تثبيت المكتبات إكمالًا للمقارنة. لا تشغل API ذات تنزيلات تلقائية بدل تجهيز الأوزان محليًا.

## التحقق المحلي

```bash
cd /workspace/SmartGov-IDA
/workspace/smartgov-venv/bin/python -m pytest -q
/workspace/smartgov-venv/bin/python scripts/verify_offline_reading.py
OMP_NUM_THREADS=1 /workspace/smartgov-venv/bin/python scripts/evaluate_reading.py --model-dir /workspace/smartgov-models/tesseract --corpus /workspace/smartgov-corpus/v1 --out evaluation/results/tesseract-development.json --offline
```

التحقق المحجوب يتطلب strace مثبتًا. لا يغير إعدادات شبكة النظام، بل يحجب عمليات المقابس للعملية المختبرة وفروعها فقط. لا تُعدّل إعدادات proxy أو TLS؛ لا حاجة لها وقت القراءة. ملف النتائج يحدد نطاق التحقق وزمنه وأمره ورمز الخروج. تستخدم اختبارات التكامل الأصول المحلية الحقيقية وتفشل عند غيابها بدل استبدالها بنتائج وهمية. يمكن تغيير مسارات الاختبارات بمتغيري SMARTGOV_CORPUS وSMARTGOV_TESSDATA؛ أوامر التقييم تقبل المسارات صراحة.

يجب تحميل القارئ والمحرك مرة عند بدء العملية وإعادة استخدامهما، لا استدعاء التحميل لكل ملف:

```python
from pathlib import Path
from uuid import uuid4
from archive_system.document_reading.reader import DocumentReader
from archive_system.ocr.tesseract import get_tesseract

engine = get_tesseract(Path('/workspace/smartgov-models/tesseract'))
reader = DocumentReader(engine)
result = reader.read(Path('/workspace/smartgov-corpus/v1/development-letter-01.png'),
                     document_id=uuid4(), run_id=uuid4())
```

هذا مدخل برمجي للقراءة فقط، وليس أرشفة أو حفظًا للمحاولات. لا تسجل نص مستندات المستخدمين. نتائج التقييم المخزنة هنا نصوص اصطناعية مسموحة فقط. تشغيل OCR الحالي لا يعني اجتياز بوابة الجودة أو اعتماد المحرك.
