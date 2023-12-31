cat Flowchart.dot > tmp.dot
sed -i 's/phy_color/"#E0FFFF"/g' tmp.dot
sed -i 's/dll_color/"#FFE0FF"/g' tmp.dot
sed -i 's/tl_color/"#FFFFE0"/g' tmp.dot
dot -Tpdf tmp.dot -o Flowchart.pdf
dot -Tsvg tmp.dot -o Flowchart.svg
rm tmp.dot

dot -Tpdf LTSSM.dot -o LTSSM.pdf
dot -Tsvg LTSSM.dot -o LTSSM.svg