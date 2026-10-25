--D－HERO ディスクガイ
--Destiny HERO - Disk Commander (texto de 2008)
--Texto del cliente 2008 (merge.dat, card_desc_e): "When this card is Special Summoned from the Graveyard, draw 2 cards."
--Diferencias con CardScripts (errata moderna): (1) fuera 'Cannot be Special Summoned from the GY the turn this card was
--sent to the GY' (el segundo efecto continuo y su FlagEffect); (2) fuera 'once per Duel' (el CountLimit con
--EFFECT_COUNT_CODE_DUEL); (3) el texto no dice 'You can': robar es OBLIGATORIO (TRIGGER_F, antes TRIGGER_O).
local s,id=GetID()
function s.initial_effect(c)
	--draw
	local e1=Effect.CreateEffect(c)
	e1:SetDescription(aux.Stringid(id,0))
	e1:SetCategory(CATEGORY_DRAW)
	e1:SetType(EFFECT_TYPE_SINGLE+EFFECT_TYPE_TRIGGER_F)
	e1:SetProperty(EFFECT_FLAG_PLAYER_TARGET+EFFECT_FLAG_DELAY)
	e1:SetCode(EVENT_SPSUMMON_SUCCESS)
	e1:SetCondition(s.condition)
	e1:SetTarget(s.target)
	e1:SetOperation(s.operation)
	c:RegisterEffect(e1)
end
function s.condition(e,tp,eg,ep,ev,re,r,rp)
	return e:GetHandler():GetPreviousLocation()==LOCATION_GRAVE
end
function s.target(e,tp,eg,ep,ev,re,r,rp,chk)
	if chk==0 then return true end
	Duel.SetTargetPlayer(tp)
	Duel.SetTargetParam(2)
	Duel.SetOperationInfo(0,CATEGORY_DRAW,nil,0,tp,2)
end
function s.operation(e,tp,eg,ep,ev,re,r,rp)
	local p,d=Duel.GetChainInfo(0,CHAININFO_TARGET_PLAYER,CHAININFO_TARGET_PARAM)
	Duel.Draw(p,d,REASON_EFFECT)
end
