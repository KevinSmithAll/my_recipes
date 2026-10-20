(() => {
  const downloadButton = document.querySelector("#download-recipe-image");
  const recipeDataElement = document.querySelector("#recipe-export-data");
  if (!downloadButton || !recipeDataElement) return;
  const recipe = JSON.parse(recipeDataElement.textContent);

  const width = 1200;
  const margin = 64;
  const contentWidth = width - margin * 2;
  const lineHeight = 36;
  const sectionGap = 32;
  const colors = {
    background: "#f3f5f1",
    card: "#ffffff",
    ink: "#26312d",
    muted: "#66736e",
    accent: "#2e6a57",
    line: "#dce3dc",
  };
  const titleFont = '700 42px Georgia, "Times New Roman", serif';
  const bodyFont = '25px system-ui, -apple-system, "Segoe UI", sans-serif';
  const sectionFont = '700 29px system-ui, -apple-system, "Segoe UI", sans-serif';
  const metaFont = '22px system-ui, -apple-system, "Segoe UI", sans-serif';

  function field(name) {
    return String(recipe[name] ?? "").trim();
  }

  function wrapText(context, text, maxWidth) {
    const words = text.split(/\s+/).flatMap((word) => {
      if (context.measureText(word).width <= maxWidth) return [word];
      const parts = [];
      let part = "";
      for (const character of word) {
        if (part && context.measureText(part + character).width > maxWidth) {
          parts.push(part);
          part = character;
        } else {
          part += character;
        }
      }
      if (part) parts.push(part);
      return parts;
    });
    const lines = [];
    let line = "";
    for (const word of words) {
      const next = line ? `${line} ${word}` : word;
      if (line && context.measureText(next).width > maxWidth) {
        lines.push(line);
        line = word;
      } else {
        line = next;
      }
    }
    if (line) lines.push(line);
    return lines.length ? lines : [""];
  }

  function createSections() {
    const ingredients = Array.isArray(recipe.ingredients) ? recipe.ingredients : [];
    const steps = Array.isArray(recipe.steps) ? recipe.steps : [];
    const sections = [];
    if (field("description")) sections.push({ title: "Описание", lines: [field("description")] });
    if (ingredients.length) sections.push({ title: "Ингредиенты", lines: ingredients });
    if (steps.length) {
      sections.push({
        title: "Приготовление",
        lines: steps.map((step, index) => `${index + 1}. ${step}`),
      });
    }
    if (field("notes")) sections.push({ title: "Заметки", lines: [field("notes")] });
    return sections;
  }

  function drawRecipe() {
    const measureCanvas = document.createElement("canvas");
    const measure = measureCanvas.getContext("2d");
    if (!measure) throw new Error("Браузер не поддерживает создание изображения.");
    const sections = createSections();
    const title = field("title") || "Новый рецепт";
    const meta = [
      field("category"),
      field("servings") ? `${field("servings")} порц.` : "",
      field("prep_min") ? `Подготовка ${field("prep_min")} мин` : "",
      field("cook_min") ? `Готовка ${field("cook_min")} мин` : "",
      Number(field("rating")) ? `${"★".repeat(Number(field("rating")))}` : "",
    ].filter(Boolean).join(" · ");
    measure.font = titleFont;
    const titleLines = wrapText(measure, title, contentWidth);
    measure.font = metaFont;
    const metaLines = meta ? wrapText(measure, meta, contentWidth) : [];
    const headerHeight = margin + 40 + titleLines.length * 52
      + (metaLines.length ? 20 + metaLines.length * 34 : 0) + 24;
    let sectionHeight = 0;
    for (const section of sections) {
      measure.font = bodyFont;
      const lineCount = section.lines.reduce(
        (count, line) => count + wrapText(measure, line, contentWidth - 48).length,
        0,
      );
      sectionHeight += 56 + lineCount * lineHeight + 24;
    }
    const bottomMargin = 56;
    const height = headerHeight + sectionHeight + bottomMargin;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Не удалось подготовить изображение.");

    context.fillStyle = colors.background;
    context.fillRect(0, 0, width, height);
    context.fillStyle = colors.accent;
    context.fillRect(0, 0, 12, height);
    context.fillStyle = colors.ink;
    context.font = titleFont;
    let y = margin + 40;
    for (const line of titleLines) {
      context.fillText(line, margin, y);
      y += 52;
    }
    if (meta) {
      context.fillStyle = colors.muted;
      context.font = metaFont;
      y += 20;
      for (const line of metaLines) {
        context.fillText(line, margin, y);
        y += 34;
      }
    }
    y += 24;

    for (const section of sections) {
      context.font = bodyFont;
      const sectionLines = section.lines.flatMap((line) => wrapText(context, line, contentWidth - 48));
      const blockHeight = 56 + sectionLines.length * lineHeight + 24;
      context.fillStyle = colors.card;
      context.fillRect(margin, y, contentWidth, blockHeight);
      context.fillStyle = colors.accent;
      context.fillRect(margin, y, 7, blockHeight);
      context.fillStyle = colors.accent;
      context.font = sectionFont;
      context.fillText(section.title, margin + 24, y + 38);
      context.fillStyle = colors.ink;
      context.font = bodyFont;
      let textY = y + 82;
      for (const line of sectionLines) {
        context.fillText(line, margin + 24, textY);
        textY += lineHeight;
      }
      y += blockHeight + sectionGap;
    }
    return canvas;
  }

  downloadButton.addEventListener("click", () => {
    try {
      const canvas = drawRecipe();
      canvas.toBlob((blob) => {
        if (!blob) {
          window.alert("Не удалось создать PNG-файл.");
          return;
        }
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        const safeName = (field("title") || "recipe")
          .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
          .replace(/\s+/g, "-")
          .slice(0, 80);
        link.download = `${safeName || "recipe"}.png`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      }, "image/png");
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Не удалось создать изображение.");
    }
  });
})();
