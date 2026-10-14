"""Extractor de primer arranque: saca del merge.dat del ANFITRIÓN los datos del juego que lee el servidor.

    python extraer_juego.py --juego <carpeta del cliente 2008 con yo2.exe y merge.dat> --salida <carpeta>
    python extraer_juego.py ... --comprobar <carpeta de referencia>     (además compara byte a byte con otra copia)
    python extraer_juego.py --listar                                     (la lista de ficheros con tamaño y sha256)

Por qué: en el paquete del Discord NO va ningún fichero de Konami. Cada anfitrión tiene el cliente 2008 (yo2.exe
Ver.080912) y de SU merge.dat se sacan los ~260 KB que el servidor necesita (catálogo de sobres, contenido de sobres y
mazos, tabla de cartas y mapa de bits del perfil). Lista y de dónde sale cada uno: paquete/DATOS_JUEGO.md.

Contrato con YO2_JUEGO_DATOS: la salida tiene EXACTAMENTE las mismas subrutas que hoy hay bajo
_original/extracted/merge2008/data (p. ej. <salida>/cardpack/packinfo_s.txt, <salida>/bin#/card_prop.bin): es la entrada
«data\\...» de merge.dat sin el «data\\» del principio.

- Comprueba que yo2.exe es Ver.080912 (la cadena «Ver.080912.NN» del propio exe, como el lanzador). Si no, avisa en inglés y
  sale con 2 sin escribir nada.
- Cada fichero se compara con el sha256 que tiene el original del owner (MANIFIESTO, abajo). Si alguno no cuadra o falta,
  avisa en inglés y sale con 3 (lo que sí cuadra queda escrito).
- Idempotente: solo reescribe un fichero si su contenido cambia (escritura atómica: .tmp + os.replace). Una segunda
  pasada no toca nada. Deja <salida>/.yo2-game-data.json para saber de un vistazo que la carpeta está completa.
- Stdlib pura (Python 3.12) + tools/kcej_dat.py (formato KCEJYUGI y LZSS). Unos 0,2 s.

Salidas: 0 = bien; 2 = carpeta del juego mal (sin yo2.exe/merge.dat, o no es Ver.080912); 3 = contenido distinto del
esperado (falta algo o algún sha256 no cuadra).
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import struct
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))            # tools/yo2emu/paquete
TOOLS = os.path.normpath(os.path.join(AQUI, "..", ".."))      # tools/ (kcej_dat.py)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
import kcej_dat  # noqa: E402

VERSION = "080912"
_RE_VERSION = re.compile(rb"Ver\.(\d{6})\.\d\d")
MARCA = ".yo2-game-data.json"

# Qué lee el servidor (paquete/DATOS_JUEGO.md dice quién y para qué). Rutas relativas a la raíz de datos, con «/».
# Patrones = carpeta exacta + comodín SOLO en el nombre (fnmatch del nombre, nunca de la ruta entera).
SELECCION = (
    "bin#/card_prop.bin",          # cardmap (research/traductor) vía tools/build_cards.py: id YO2 interno
    "bin#/card_name_e.bin",        # cardmap: nombres en inglés (pareja con cards.cdb)
    "bin#/card_indx_e.bin",        # cardmap: índice de esos nombres
    "common/yo2pbinmap.dat",       # research/avatar + research/persistencia: bits PBIN del perfil
    "cardpack/packinfo_s.txt",     # yo2srv/tienda (PACKINFO) + research/persistencia/duelpass: catálogo de sobres/mazos
    "cardpack/*.iid",              # yo2srv/tienda shop_purchase: cartas de cada sobre
    "cardpack/*.ydc",              # tienda (mazos), persistencia (recetas 100NN, SD_GET, DPAUTH) y mazos premontados
    # P5 (11-10): para rehacer research/ia/cards_yo2.csv en el PC del anfitrión (paquete/generar_tablas.py) en vez de
    # meterlo en el zip (lleva nombres de carta sacados del cliente).
    "bin#/card_name_s.bin",        # generar_tablas: nombres en español (columna name_es)
    "bin#/card_indx_s.bin",        # generar_tablas: índice de esos nombres
    "bin#/card_max.bin",           # generar_tablas: copias permitidas (lista de 2008, columna limit)
    "bin#/card_pass.bin",          # generar_tablas: passcodes candidatos (cruce con cards.cdb)
    # PUB (12-10): el resto de tablas que salen del cliente también se rehacen aquí (paquete/generar_tablas.py)
    "avatar/npc/fuki_index.csv",   # generar_tablas: npc_decks.json (fuki_vacios: huecos de diálogo sin texto)
    "common/yo2text_s.idx",        # generar_tablas: titles.json (nombre en español de cada título)
    "common/yo2text_s.bin",        # generar_tablas: titles.json
)

# ruta  tamaño  sha256 — sacado de _original/extracted/merge2008/data (merge.dat del cliente Ver.080912 del owner,
# sha256 235c7fbd…9dbc9b, 691.359.533 B). 176 ficheros, 897.233 B (169 del servidor + 4 de generar_tablas, P5, + 3 de
# generar_tablas, PUB 12-10).
MANIFIESTO = """
avatar/npc/fuki_index.csv 54670 53029aa0fec4492dc3c3690cccf596c3d11c208659b78b6fef1e017f4f5a0959
common/yo2text_s.idx 128000 8af64e51f590bac26fcc3dc1dbfc80a9c1bc06bbea0c32dc2673d470a519e008
common/yo2text_s.bin 277938 fb9cd53348848f1a64dcc5bf1d5ad1b24b03cd9ca1cffbaff0646a7fd76711cf
bin#/card_name_s.bin 132756 6c4c57ec00d500c99aa6605757d153c2e9cdca88187965ba94d20af2683fc394
bin#/card_indx_s.bin 26056 a376561eef8eeed0353a23c18008307f139c61533856b5e451e74f6d8af28ea4
bin#/card_max.bin 6512 e7fa2cb59f9e4751377b6e3d138f6c6800fbdcf5f62b684b547525cf8249baf3
bin#/card_pass.bin 9692 a629d66f9a3ccb45dc49531a6774a4e5541188c6d9bc42b636b5b9502bbfc736
bin#/card_indx_e.bin 26056 3131392fcb1a22d57449a11cdaf14fb22e6745a6f3e3defde10da9b97ebbcd9b
bin#/card_name_e.bin 115160 49380ee1eedac41d0661df7ff7609e9c963f33a2a52d74a3e4998d373f195336
bin#/card_prop.bin 26048 b89c925dff3d71a68397b16f03f3cafaebb6caac7be653c777ae912958bb075d
common/yo2pbinmap.dat 46164 02941b2a5dc545c76f00da6597b9cfa2d68028405eb8c979601a07f962905812
cardpack/packinfo_s.txt 6097 3e44139ae59890b480b4dc114f58dd129e1aa65dcff3debedee912c1688a90c2
cardpack/1.iid 88 e17af2b79ea6b73f8527498866ed410a95b2e2113579fc86c37c7566c050b4ad
cardpack/10.iid 110 3604a9de41b28ce48c076652d09475c71ca1a3104e1c55adf1ce7b233f588384
cardpack/10001.ydc 250 81f6b2fac9b0e1d2cb3cd9c3d1924b99b288efc75263d94e7dcd049a40734d02
cardpack/10002.ydc 250 367feeb0fd50fa306155805bff6ef05e4a0d808d942171bc587d1793d5060883
cardpack/10003.ydc 250 d2dd8e9578235cfb3577cecbc448658602925944f0a19ab2d6abedc3a2f933cb
cardpack/10004.ydc 250 710a9c6442716ea182d3a3e6ee83844182e47ed8839690c7ff8a9c5f79298535
cardpack/10005.ydc 250 f8941e7f741dd9e8cac33de2afe1ce72d051720251d7515f53706f35b19c4923
cardpack/10006.ydc 250 597eac40c1d89bde6b9147074c12d89415707a2e1b8c2bebe480328c1c4ae264
cardpack/10007.ydc 250 1d18b575f297f7ca0f45714dfaaebf68f6c9262c5870bce288325fe1fd1b96a1
cardpack/10008.ydc 250 e56ce886bfdd556aee9ff21e0c03280bab8960c527a04357d4218d1b2f099712
cardpack/10009.ydc 250 4b8bb414a8fc1577befe937ba569ff1cdc18aaacb914bd344821bc0dee148b18
cardpack/1001.iid 300 c96867595508b978c264bd469b3712720768cabfa70cc232417cdf3d5431bd1b
cardpack/10011.ydc 250 e813f8ae0891470b33354e2914be2af0889cfcc598361feb25ae0137b5a5c65f
cardpack/10012.ydc 250 c76d80be4da0cca5bbf664f55f2815ce63b1ad1a42d2687117b1482f852df379
cardpack/10014.ydc 250 f1a071facd65f64823d3fd6641b08bffb800f67363815993648ef44aa945d1e9
cardpack/10016.ydc 250 31a47e11960b1b9028bce7d08d67d9a96cd55933d1991bdb12718e9b8c57b20b
cardpack/10017.ydc 250 64d9f36aba25f8d6773c8669af55ceda5575c39355bdf9229f0a27fa62672a38
cardpack/1002.iid 312 3df1938dfc3b9392d96ec83f23ea0ae13d76bef62b32fa93ea5a6090a43cb7d5
cardpack/10020.ydc 250 024377d1cab5c924649d51d9410e223babf44946d06b3481d593d472087da105
cardpack/10021.ydc 250 811f6fae98f82d2472d4a6050926c999bf17228a514430464111c8e456b05e6b
cardpack/10024.ydc 250 f0eadae3f7a01bdf5b6ff5d89454eae96bf397868550dd44581a49854e0fc55f
cardpack/10026.ydc 250 4cf021d2e510bf5bf24c7c03dd977271cb869ce4ef4c8ff26acbaf12bf6b74ac
cardpack/10027.ydc 250 8ba533d6814de6b66683f4d736b122bf0a05f6aa01b1d5066d1fd2fbbb623803
cardpack/10028.ydc 250 0d9566ccc74e0e7dc6a15e31e060d4087fc652f7117d2f44362c912f99f84798
cardpack/10029.ydc 250 bb804dd0f197de03afa410551620e9819d8d4e838b1f30334b87174e174d5e65
cardpack/1003.iid 310 36c77b1aec441bb19f8e038d16ab045881222febe2765e05f0ed21275685dd85
cardpack/10030.ydc 250 d95215ac1f0f2235cfbd9941e1370b4677e7c919d736af37d74af10b77423a4d
cardpack/10031.ydc 250 3cf2024200f5de4f8a7a8797656f3e8ca720533f262981990b7b466f4ebff236
cardpack/10032.ydc 250 01c75acf865677771b52dc837b5f613106be8dbb571c1d367dea03b19ebab3b7
cardpack/10033.ydc 250 195ab06d1cf76169291266f1f543d99839c86f1ff81e06c4fabefb2f1246a3eb
cardpack/10034.ydc 250 7e1c818b92cacd07dfdff1433c9c58a91b7c641cded9c7f60785995958a53c49
cardpack/10035.ydc 250 fd5afde2ae396baaab7931f00b6b5c66849eb511e973a30fcef1c1a67b2bf7e3
cardpack/10036.ydc 250 b5ce18241d8ed4698f421d4829b898293d963f2d833b38d3181dd0fac88e36fe
cardpack/10038.ydc 250 0a21e1a6e9938c791891ee93fffdef5b6bd5c218ffe856444e6d10c65446b3b0
cardpack/10040.ydc 250 016b68e8c325abcc5e65142a969c1f3d81c1efbc0b02c383a7ac41fab57d5a66
cardpack/10043.ydc 250 09459b4a8d7f13866968681226dc3a7ea7d73ed86781162fee814808b04a4237
cardpack/10044.ydc 250 010517423be412ced2ad857d1708eb3a4e38b4ae16f44b9ce9aa7f7fa7ea5572
cardpack/10049.ydc 250 f2981ef8dee36f6d9ce39b3830cb75e997d41f5b1f4d0bea5f69cd4e6fb99672
cardpack/10051.ydc 250 3cf3c7002719e60a012b4ce0a566604f3468434ef544410d421c6ee58fcfb0d8
cardpack/10052.ydc 250 4cf3ea6c009f82a783604e89f0509ca6180f7994df7c0d3576af2e9c063825b1
cardpack/10053.ydc 250 4bd01a1d4c365ebc257441e9cd4d44d91ee13697ac3be15a17b5c431ee3e174f
cardpack/10054.ydc 250 3286312c0b92702611f0f3dcb655f15b07470afc2960455a146631bf6c228b38
cardpack/10055.ydc 250 1e2719327df8e91a3d127a9ccb218d41fcdf0e04a02d0f5d61146b1af750a8b1
cardpack/10056.ydc 250 9de812586506955d2f931ec1f38793bb232b77f05249a12f338119444fb4d663
cardpack/10057.ydc 250 466bb3f1b72810a1a1c5fdb03b059d736427c35a0334cb0711baabf87214256b
cardpack/10058.ydc 250 f09113dd68c7fddf30a878b2968a86bc91a4dfeda454c74df7a282d717408d55
cardpack/10059.ydc 250 deddd0c7a5cd7f97e5cb7856238f19754111e62f3ff897539495671f0ad3e1fa
cardpack/10060.ydc 250 2721c7a67bfaa794bebc4729ea38538d09fa626aa5e59c21ac9c8bceea772eb2
cardpack/10061.ydc 250 d2e80f324d2b95bfe0d0aa5d461e16790c19877b0f7d642531f121e35ac768b2
cardpack/10062.ydc 250 b3d4333886c4465497f806560f5a11e382847225ab7c864dff4edf5a3a920401
cardpack/10063.ydc 250 c7a6ee5ff9972e4f001581c92aa14513d7328d7fdba40f3e166fd366e43ebce1
cardpack/10064.ydc 250 e0baf58dfc398e3fe138745ce4da61e5faaacb95d4816d8b221fcf46ccd96c59
cardpack/10065.ydc 250 205468ef1836a0d135dd2969c8df3ec022792627f861a7d47fa7f8434c817db7
cardpack/10066.ydc 250 24c9ecb9949de70cda8a1239ffe9c61a3a15c2f0379226952873e7807c521f8c
cardpack/10067.ydc 250 d294f6a16a8fb70027e998ed7cea582feadafa4d577050616a53a7789a56abb0
cardpack/10068.ydc 250 05b1f0db7619e1e3a9233b82b1597691d6463e65790bbe5ca4e68e7504cfc166
cardpack/10069.ydc 250 41e0a89c8912680f4a870a84df178d91dbea4cc159950594b7a9cb8e8749d11a
cardpack/10070.ydc 250 70ea2f0c8a96a10733d27ae1eb6b8ce22bc0a5b166be5ba3d4a62c22d31e21a8
cardpack/10071.ydc 250 9b521f56f5522c2bd373c7d822b8b2c9264f4951df202b31c8c8c4e0f32e58c5
cardpack/10072.ydc 250 ee4d373c39db92109633fbd6296681ec3e43fd64c4c811128582f63108f0b9e0
cardpack/10073.ydc 250 6e2aeb9f05b28f74f0d71113a6d7da51a466a1d92d0ea71d16ee9420e2acfc73
cardpack/10074.ydc 250 552162ac63e8a4cf70fad5764c8e64afd35ea8b2dacb5abc6e91494f6c8e753b
cardpack/10075.ydc 250 574c0dfe82087c41d849be9717f304e41169d75eac08bc37941ddd24b29cd2c6
cardpack/10076.ydc 250 048d008e8c730fc685bc62687f5ce59213fa65d95bd1acb02d4f169cc4970fba
cardpack/10077.ydc 250 446523664c85e625eb1c941f3446075dc7032d21f87960aaa8fde4aad0ba631d
cardpack/10078.ydc 250 fa074341b69a987332758eb5b6375bd3529be6d66c28f0c78c3bce8f37903ca7
cardpack/10079.ydc 250 619dd463deca1b3f75a8f65d73f6d95d8196d334de70c1cbce5d748cec51edfe
cardpack/10080.ydc 250 20ac45af4ae5db8db65531667d2a6c8aed4e81f285efe0da21c33bcfd60929cd
cardpack/10081.ydc 250 b0ec06fd594dbcf84455939cc566598b19ea7751b6915996a9d2108739456191
cardpack/10082.ydc 250 018c95cfe6524e3512943de56525c3cb6b05abdc01c09e29311abb68463db132
cardpack/10083.ydc 250 965566fda6884417cfb1f81b27fc814435eb2847af6c7ae3583e50e6663ddb96
cardpack/10084.ydc 250 208ad029907a52ccce05ba4874d942edef644ec8d19479b8c9d417d7a5d84a94
cardpack/10085.ydc 250 1b946dccc1eb28ab099ff59ae682e82dc0e70e713167f44bd7b21e6df22a408d
cardpack/10086.ydc 250 5c51d25da28b91eec96cbe9daa1394f7176c397050f4410b3ca7d9c670ebdf52
cardpack/10096.ydc 250 666ea18c28fe3e8c7b6dee38f547b4d6db2d496a364d9c788c6403f5595bbbeb
cardpack/10097.ydc 250 d5e1785531884dd8b4a6cf1e08552d2a42b0538c281c63a6c38509e7683f0f54
cardpack/10098.ydc 250 b4e0fd22b16535ed3c112ad46e98457be2a4348e037b95910c203f30fd582979
cardpack/11.iid 90 e1be537a4655ce0142317b14af3f95f8a63dfb3f00f7b7166cafe9894417a5f6
cardpack/11001.iid 20 94a3cd345ef336ce84394e57f8e058c831e0d184bfb9d74434bdf5255651400c
cardpack/11002.iid 20 7d508de53878396011f246dc5d4eee344240d4bcba7950f0c49acf9e3b542217
cardpack/11003.iid 20 85fc0f29c452d001337335dc7e28a68c22e812260a222a33e5b00fa83d58de45
cardpack/11004.iid 20 b498c11413df6ba6c0ce00ba4502e8c132b31c2602360cf84ca33f46636fe501
cardpack/11005.iid 20 d7d08928ea8fbf919ec1d2433508be07b71b69b3c482d3c710f806f3b785cd51
cardpack/11006.iid 20 52fdff912f83f684fa600a5f450746978a991342424ba5e178aae201766d1c3c
cardpack/11007.iid 20 fdca40a9cef2981bcf8fda3be0808350c9ba87cca181a055f4298eb62960edf2
cardpack/11008.iid 20 574e1fc56c56919a56c6012e56952cc55a434eec24678fce7adc4f15bbebcca1
cardpack/11009.iid 20 0b4c0861251b152eadcf797f2743a2841c2c9aad11c2c6bdb9bee80dbfd650ab
cardpack/11010.iid 20 732e4738f6cab92e0b00b72c22b20b0832a8fdb39743f806e63afbb7a850e6c8
cardpack/11011.iid 20 7b534fea5b101a7d359aabd1d576c0dfd39b367ddf57337ece0d579d02f50a39
cardpack/11012.iid 20 7cb85e00713984b315b51795085383d138b637a5316dbb8974426957fd4f79f3
cardpack/11500.iid 40 19feec9a2587873649a73fd52da13ee0e16248e61f4b84f38af211cf7f6c35d1
cardpack/12.iid 90 121804ee25b1799d57b5a07ff36a0e50d706d5c4870cbc760ab339c3044d6dfb
cardpack/12001.iid 80 ddaae582298cdd72b3f8c96cb752ed47068da5075425a327b8847e52d1094e64
cardpack/12002.iid 80 92c5b0323dce8ad419973420aa141c952d422711349c77073dfaa30c1ba15255
cardpack/12003.iid 80 2253e35e679e8c0a1589becf3fc9239d8ee9bcc063485c2979a7e96c4db85f26
cardpack/12004.iid 80 2ea21ba937416387192812d75dfec1d883cc951502f3a36f6bc999aa46f8acef
cardpack/12005.iid 80 d559884c297bb396efb0416dd6078e4570d73fdcdda76e066afcce149ec5dccd
cardpack/12006.iid 80 3d4834f45a1ea8c21408dd2431685a720d5c25b839ca2e46bb0e0ec3833567b3
cardpack/12007.iid 80 e2a92805198cc444698daa06abc998d9c6d65778d2947668ae7768740f8c4647
cardpack/12008.iid 80 fba8f14852bbb615aa3813db640b3d5f673f0ffdc1416dd221f567f94148bca5
cardpack/12009.iid 80 224beab2b4d29584eca773264dd9fd95cfc7e4e87646f03ee39f9bb617c12a4f
cardpack/12010.iid 80 fd2f0f5ecfe4b0ed429bf44caf4053bace810547f3b3e6daa9583c46d93a9a7a
cardpack/12011.iid 80 84dc5448829d4c47bf30718bb05037911a2743f636e5b3bd1e0a22d7d7cd3c49
cardpack/13.iid 90 bd9d55f90bfb88996d0674eaba52d0968934c445beffdd7904bffbd900ef38c1
cardpack/14.iid 90 8846551b7dc4a71f2ff212679ad8a994caaec5048f4aec009ce2012916823c2e
cardpack/15.iid 108 9c801d498f5150afda3b2d61dadbb24892d1b8ac471060375ee7f01178f78e8d
cardpack/16.iid 104 84dc4756b2dc32e02330bbc4c794ccefe18dc6555d76fbaa6c21b13d9af5d0cc
cardpack/17.iid 104 03fe9a335ca4981d2240bfe6836e46ea8d524f17c6bec3492e0075ca552cc5bb
cardpack/18.iid 104 247679d7744429d349f805e1b9c842b78eaf571bd085f3a76710b7aaade7bdae
cardpack/19.iid 104 5c7d970aa5778cb80438948d08affd9c5d2c640d85c8815015a92ea55a47d58a
cardpack/2.iid 88 f19abd9dafb499119244d447dce3333621b371bcd144550a23a059d3087a733a
cardpack/20.iid 104 387729115b84887237c8ec946852e412a346750424d27450d1e3bcdb5bf4b4b8
cardpack/21.iid 102 737c2e68aba9e78a53a78658e9774f96fe5c0a08d1f4b43332c9b0d51aa45e8b
cardpack/22.iid 106 b4f6a97b895123c6924f09b0b4d68cebb7df1d0a490795d486e296219c585d11
cardpack/23.iid 106 26004a21adf48ac1d1321e7c8b54c273fa00c0458dd7f8104520b1e1a3f0726d
cardpack/24.iid 114 7ffa616b1b4d59dbe906e60f11179fc1a1c8fa9f5ae75e813e6efb8392ebed7c
cardpack/25.iid 106 8bfaae8eef42a5d8e785703217bf898b8b3cc03ea06dd65a3659d08c51e84ccc
cardpack/26.iid 112 675c127141758bb5e04c5563a8c9f0c4b6a73c1ffb4d8365cf37cfd69aeb63a2
cardpack/27.iid 110 07028d6b8ccb7d308cea1541e0becf3aef295606d3bf703e8c15a200b2b330eb
cardpack/28.iid 100 485567143b6941758ddea767c094a54c0efbd929416e5c537f8d21e6077ea796
cardpack/29.iid 100 81c24f69b90bad3dda45e425ebc91b692985ea08cdc2f83fefd397cf4e2908b5
cardpack/3.iid 88 4aee66e416e055ffef19ec61c8780541b3560f69c820e5ee79f8befcd9b1b722
cardpack/30.iid 102 3c44f6abb9b0b2b09031449739b6e5d3383eb152aff6fa99246a125cd09c198e
cardpack/31.iid 112 98eb5a4666fee8a08139bb94470ff2fd4220d392abbd63b3115c65a5f4378a0d
cardpack/32.iid 112 21932b065bef0b0510e2831b29990569784c3c716ffceb781c986a2a9ccf0011
cardpack/33.iid 120 fd6a961781a3adaf44852e569d16c065c6a0507918eff3ca6741544297bd9d63
cardpack/34.iid 120 6e63f212d25626e300b4fffdb1c9d9e21b836f821dd4fa6019bdedf6fd9028db
cardpack/35.iid 118 499a039fcbae8dfcdabf1cee59f26e8d03ac312adc8f93e7b746f42db570b671
cardpack/36.iid 118 e2436e5e1365b700f5d6c2135cbd6bfa1ccc156d022157081a4023117c671d0e
cardpack/37.iid 118 6d38d9a5973ba21044d3e8f4d469599df0525297ad5bb606726e54d4826ce77b
cardpack/38.iid 120 8ed74ef6240f1819ae730aae36c3e0887d758728d96698f45a786b250f7adbcd
cardpack/39.iid 120 fdfb56675ec96a556e139368be2b044619a51b88031e908d1ad0c9a45274cc46
cardpack/4.iid 88 b49b7b62061d2cd1046fb32b0771eb82f628a59ecfebd7de484db4420a598865
cardpack/40.iid 120 4f4f981992d1ae2fb52306246c8bca58dc4de1a1c0c3cd74cdf2b621ff2f9570
cardpack/41.iid 120 b512179fac10f6a8f2e14eebd8fb35a2abc32069b872434a0b9a34530c4ba230
cardpack/42.iid 120 309d0adac1a2f46c1829f9175de6c03bed49ae33acba2028457eea5f3d5f49f3
cardpack/43.iid 120 057956dd4b2c5dc9c36fc96dea42440ebaed1a78fa377038c4752ed68b921bbf
cardpack/44.iid 120 44e6539e49517b42ae32f3c1eb80f28c24b0c35d2f682d2a16b1d6ceb6e6b332
cardpack/45.iid 160 4b8f71af27697a9a21376a1ac29d7fec265c562a36052b51575c882ca59e6c1d
cardpack/46.iid 160 be7ddec7e32297df5473840e0a5c50bdc4b9f6645cb33444c39d21a027d0be5b
cardpack/47.iid 160 c2a2f8280cae0dd399865292b4561d96bcd1e2b6ce52c2a43a1c26fca6987e9f
cardpack/48.iid 160 09f49b7424b4c84b060b77b32fb2260583d778bd238d2121ae9d1dedc70c3495
cardpack/5.iid 102 b0dc53e3b2dfaf42b09a3f7912f3a6074e166a1d4ab4f9b3036b188da2db951b
cardpack/6.iid 102 9f2fbf20657d1d2f79eb1d9d489ffd528c293cf16718dd9383a61030a564889e
cardpack/7.iid 110 fffc994c48373edd9757ac726d8ae31a4695ccfc115df22a479d85734177bf72
cardpack/7001.ydc 250 bd00aef3693e91d0b1232c35e1cd8d43443b8b681cab715cfe78061af24c2d2b
cardpack/7003.ydc 250 ffc915d01825469c007d6743f350964858d8e82376772e00bb2e3eae5155f72b
cardpack/7005.ydc 250 fc0370bf1f4b1b85a343a38e3363cf5f3567970573a2852333496880d9f66409
cardpack/7007.ydc 250 62982d5dea5f9c497e15125ab2231c300950208d2a2bec8b42da6d49d13caa2a
cardpack/7009.ydc 250 c54f3d440c1632e11ce3ff87ed21011df1feb99d8c7a956aafe0678f2eb66bad
cardpack/7011.ydc 250 3370a3fa45f063871eecc0e398c0d7c3f705776cb2b7e56073cde5b21ccb7829
cardpack/7013.ydc 250 3e7b0580996ce00248d7366c5c64291ebf3d8b259ab1d6ca932d4bbdf7b23ed4
cardpack/7015.ydc 250 4376b72b8c71066b66070d27086f3158942768d49a4b7b1aa55e403b158493f9
cardpack/8.iid 110 aa48617f30f843b17b172fb9e1f32b4bbb1ede22f8b1587692aa4da1ab6dcd84
cardpack/8001.ydc 250 a5af75e03d09506de12ca042f3f67e07795171b14bec7d7ee7d861985f281167
cardpack/8002.ydc 250 9e9313f24c9e0c7028b17e0fcb5dce5eb2e83659e498e12afc2699bc2d05b66d
cardpack/8003.ydc 250 66690dfbf37a0ff2772374168b5b7c949cbe95c09a9baa2fb11a1ae4510749c7
cardpack/8004.ydc 250 b458cc12481b04fc5be586ccc1e084866e7820e3dbe90f447c4454946cc51ee0
cardpack/8005.ydc 250 3e522801ed128ec2e6384660b9301a2a528f88d693f71558740a3163622d6599
cardpack/8006.ydc 250 4eaf97d56d7690ad2b58df0adf518912a0015fcd11f58c7c1c9110b28174618c
cardpack/8999.iid 4738 ca2dfe46fd2b374b6dd6f8c3ef805ff24b801c1987a22167b0da5471d741ba3a
cardpack/9.iid 110 5b4b78b58ca19e8c15c8301178f981290bf0fd45952f184dd08e4cda250f2358
cardpack/9001.iid 5486 1a701633b88e19f9769bc35d81dc0d16650c1d64e82f21e443661bf6d23e5837
cardpack/9002.iid 2938 0a5d393c02d87c45f63df43ab3ab6f1d627c63a7f79b966ebbb4fd0e01db3a52
"""


def manifiesto():
    """{ruta en minúsculas: (ruta, tamaño, sha256)}."""
    out = {}
    for linea in MANIFIESTO.strip().splitlines():
        ruta, tam, sha = linea.split()
        out[ruta.lower()] = (ruta, int(tam), sha)
    return out


def elegido(rel):
    """¿La ruta relativa (con «/», minúsculas) es de las que lee el servidor?"""
    carpeta, nombre = os.path.split(rel)
    for pat in SELECCION:
        pc, pn = os.path.split(pat.lower())
        if carpeta == pc and fnmatch.fnmatchcase(nombre, pn):
            return True
    return False


def version_cliente(juego):
    """'080912' o None si yo2.exe no lleva «Ver.AAMMDD.NN»."""
    with open(os.path.join(juego, "yo2.exe"), "rb") as f:
        m = _RE_VERSION.search(f.read())
    return m.group(1).decode() if m else None


def entradas(merge):
    """[(ruta relativa con su caja original y «/», offset, tamaño, guardado)] de las entradas data\\... elegidas."""
    out = []
    for nombre, off, tam, guardado in kcej_dat.read_toc(merge):
        rel = nombre.replace("\\", "/")
        if not rel.lower().startswith("data/"):
            continue
        rel = rel[5:]
        partes = rel.split("/")
        if any(p in ("", ".", "..") for p in partes) or ":" in rel:     # nada fuera de la carpeta de salida
            continue
        if elegido(rel.lower()):
            out.append((rel, off, tam, guardado))
    return out


def escribir_si_cambia(ruta, datos):
    """True si escribe (no existía o era distinto). Atómico: .tmp + os.replace."""
    try:
        with open(ruta, "rb") as f:
            if f.read() == datos:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "wb") as f:
        f.write(datos)
    os.replace(tmp, ruta)
    return True


def extraer(juego, salida):
    """Devuelve (código de salida, resumen)."""
    t0 = time.perf_counter()
    juego = os.path.abspath(juego)
    exe, merge = os.path.join(juego, "yo2.exe"), os.path.join(juego, "merge.dat")
    if not os.path.isfile(exe):
        print(f"ERROR: {juego} is not a Yu-Gi-Oh! Online 2 game folder (yo2.exe is not there).")
        return 2, None
    ver = version_cliente(juego)       # antes que merge.dat: el cliente de 2006 (Ver.060419) no lo tiene y así se dice por qué
    if ver != VERSION:
        print(f"ERROR: the game in {juego} is {'Ver.' + ver if ver else 'an unknown version'}, but the server needs "
              f"the 2008 client, yo2.exe Ver.{VERSION}. Choose the folder of that client.")
        return 2, None
    if not os.path.isfile(merge):
        print(f"ERROR: merge.dat is missing in {juego}. Copy the complete Ver.{VERSION} client folder again.")
        return 2, None
    try:
        lista = entradas(merge)
    except (AssertionError, OSError, struct.error) as e:
        print(f"ERROR: could not read {merge} ({e}). The file may be damaged: reinstall or copy the game again.")
        return 2, None

    esperado = manifiesto()
    salida = os.path.abspath(salida)
    escritos, iguales, malos, extra = 0, 0, [], []
    vistos = set()
    with open(merge, "rb") as f:
        for rel, off, tam, guardado in lista:
            datos = kcej_dat.read_entry(f, off, tam, guardado)
            clave = rel.lower()
            vistos.add(clave)
            ref = esperado.get(clave)
            if ref is None:
                extra.append(rel)                  # no está en el manifiesto: se extrae igual, pero se avisa
            elif len(datos) != ref[1] or hashlib.sha256(datos).hexdigest() != ref[2]:
                malos.append(rel)
            if escribir_si_cambia(os.path.join(salida, *rel.split("/")), datos):
                escritos += 1
            else:
                iguales += 1
    faltan = sorted(v[0] for k, v in esperado.items() if k not in vistos)
    total = sum(v[1] for v in esperado.values())
    marca = {"client": VERSION, "files": len(esperado), "bytes": total,
             "manifest_sha256": hashlib.sha256(MANIFIESTO.strip().encode()).hexdigest(),
             "complete": not (faltan or malos)}
    escribir_si_cambia(os.path.join(salida, MARCA), (json.dumps(marca, indent=1) + "\n").encode())
    res = {"salida": salida, "escritos": escritos, "iguales": iguales, "faltan": faltan, "malos": malos,
           "extra": extra, "segundos": time.perf_counter() - t0}
    print(f"Game data: {len(lista)} files from merge.dat -> {salida} ({escritos} written, {iguales} already up to date, "
          f"{res['segundos']:.2f} s)")
    if extra:
        print(f"WARNING: {len(extra)} files not in the expected list (different client build?): {', '.join(extra[:5])}")
    if faltan or malos:
        if faltan:
            print(f"ERROR: {len(faltan)} files are missing from this merge.dat: {', '.join(faltan[:5])}")
        if malos:
            print(f"ERROR: {len(malos)} files differ from the expected 2008 client data: {', '.join(malos[:5])}")
        print("The server may not work with this game copy. Use an unmodified Ver.080912 client.")
        return 3, res
    return 0, res


def comprobar(salida, referencia):
    """Compara byte a byte cada fichero del manifiesto en `salida` con el de `referencia`. Devuelve los distintos."""
    distintos = []
    for ruta, tam, sha in sorted(manifiesto().values()):
        a, b = os.path.join(salida, *ruta.split("/")), os.path.join(referencia, *ruta.split("/"))
        try:
            da, db = open(a, "rb").read(), open(b, "rb").read()
        except OSError as e:
            distintos.append(f"{ruta} ({e})")
            continue
        if da != db or hashlib.sha256(da).hexdigest() != sha:
            distintos.append(ruta)
    return distintos


def main():
    ap = argparse.ArgumentParser(description="Extract the game data the YO2 server needs from the host's merge.dat.")
    ap.add_argument("--juego", help="game folder (the 2008 client, Ver.080912) with yo2.exe and merge.dat")
    ap.add_argument("--salida", help="output folder (YO2_JUEGO_DATOS)")
    ap.add_argument("--comprobar", metavar="REF", help="also compare the output byte by byte with this folder")
    ap.add_argument("--listar", action="store_true", help="print the expected file list (path, size, sha256)")
    a = ap.parse_args()
    if a.listar:
        for ruta, tam, sha in sorted(manifiesto().values()):
            print(f"{ruta}\t{tam}\t{sha}")
        return 0
    if not a.juego or not a.salida:
        ap.error("--juego and --salida are required")
    rc, _ = extraer(a.juego, a.salida)
    if rc in (0, 3) and a.comprobar:
        d = comprobar(a.salida, a.comprobar)
        print(f"Compared {len(manifiesto())} files with {a.comprobar}: "
              + ("all byte-identical" if not d else f"{len(d)} DIFFERENT: {', '.join(d[:10])}"))
        rc = rc or (3 if d else 0)
    return rc


if __name__ == "__main__":
    sys.exit(main())
